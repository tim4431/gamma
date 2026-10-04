"""Accounts: the rules for usernames, emails and passwords, creation with an
invite, the e-mail links (verify, reset, change-email), plans, deletion.

An address is stored as it was typed but is unique by the inbox it reaches
(``email_canon``), and register refuses throwaway-mail domains, so one inbox
cannot become any number of verified accounts.

Everything takes an open connection and commits nothing: the router owns
the transaction so one request is one commit. The ``Problem`` exception
carries the status and the message the API returns; the app's handler
answers it.

``accounts.plan`` is the effective plan every claim reads, and it is
computed (``recompute_plan``): the higher of ``granted_plan`` (what an admin
or an invite gave) and the plan the account's Stripe subscription pays for
while it is live (``billed_plan``). docs/dev/billing.md has the rule.
"""

import re
from contextlib import closing

import bcrypt

from . import config, db, mail, settings
from .db import after, audit, new_id, new_token, now, token_hash

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_PASSWORD = 200

# A username is the username on every Gamma server and the label of a
# paid container's hostname, so the names Gamma and the web reserve stay
# unavailable.
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "guest", "gamma", "cloud", "account", "accounts", "api", "app",
    "www", "mail", "smtp", "imap", "ftp", "ns", "ns1", "ns2", "dns", "static", "cdn", "assets", "media",
    "help", "support", "billing", "status", "docs", "blog", "dev", "test", "staging", "demo",
    "link", "share", "shares", "sync", "mcp", "oauth", "login", "logout", "register", "signup",
}

# Aliases of one inbox. Every provider below ignores a ``+tag`` suffix, and
# Gmail also ignores dots in the local part, so ``f.o.o+1@gmail.com`` and
# ``foo@gmail.com`` are the same mailbox and must be one account. A domain
# that is not listed is taken literally: a mail server is free to treat
# ``a+b`` as its own mailbox, and refusing a stranger's address because it
# resembles someone else's is worse than the duplicate.
ALIAS_OF_DOMAIN = {"googlemail.com": "gmail.com"}
PLUS_TAG_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "pm.me",
    "fastmail.com", "fastmail.fm", "zoho.com", "gmx.com", "gmx.de", "gmx.net", "mail.com",
    "yandex.com", "yandex.ru", "aol.com", "qq.com", "163.com", "126.com",
}
DOTLESS_DOMAINS = {"gmail.com"}

# Throwaway-mail services: an address there passes the verify mail but the
# inbox is open to anyone, so the round trip proves nothing. The list is
# deliberately short — the services actually used at scale — and a name also
# covers its subdomains. The Admin page's blocked-domain list extends it
# without a release. Someone with their own catch-all domain defeats any such
# list; Turnstile and the rate limits are what bound that case.
DISPOSABLE_DOMAINS = {
    "0wnd.net", "10minutemail.com", "1secmail.com", "20minutemail.it", "anonbox.net",
    "burnermail.io", "discard.email", "dispostable.com", "email-temp.com", "emailondeck.com",
    "fakeinbox.com", "getairmail.com", "getnada.com", "grr.la", "guerrillamail.biz",
    "guerrillamail.com", "guerrillamail.de", "guerrillamail.info", "guerrillamail.net",
    "guerrillamail.org", "harakirimail.com", "inboxkitten.com", "luxusmail.org",
    "mailcatch.com", "maildrop.cc", "mailinator.com", "mailnesia.com", "mailsac.com",
    "mailtemp.net", "mintemail.com", "moakt.com", "mohmal.com", "mytemp.email",
    "sharklasers.com", "spam4.me", "spambog.com", "temp-mail.io", "temp-mail.org",
    "tempmail.com", "tempmailo.com", "tempr.email", "throwawaymail.com", "tmpmail.org",
    "trashmail.com", "yopmail.com", "yopmail.fr",
}


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- validation ---------------------------------------------------------------

def norm_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise Problem(400, "That does not look like an e-mail address.")
    return email


def email_canon(email: str) -> str:
    """The inbox an address reaches, used for the uniqueness check only.
    The address itself is stored as typed, so a ``+tag`` its owner filters
    on keeps receiving the mail."""
    local, _, domain = email.rpartition("@")
    if not local:
        return email
    domain = ALIAS_OF_DOMAIN.get(domain, domain)
    if domain in PLUS_TAG_DOMAINS:
        local = local.partition("+")[0]
    if domain in DOTLESS_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else email


def check_email_domain(email: str) -> None:
    """Refuse a throwaway-mail domain at registration. Not applied to a
    reset or to an account an operator creates: an address already in use
    must keep working even once its domain lands on the list."""
    blocked = DISPOSABLE_DOMAINS | settings.blocked_email_domains()
    parts = email.rpartition("@")[2].split(".")
    # A listed name covers its subdomains, so try the domain and each parent.
    if any(".".join(parts[i:]) in blocked for i in range(len(parts) - 1)):
        raise Problem(400, "That mail provider is not accepted. Use a personal or work address.")


def norm_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise Problem(400, "A username is 3 to 32 lowercase letters, digits and hyphens, "
                           "starting and ending with a letter or digit.")
    if username in RESERVED_USERNAMES:
        raise Problem(400, "That username is reserved.")
    return username


def check_password(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) < MIN_PASSWORD:
        raise Problem(400, f"A password needs at least {MIN_PASSWORD} characters.")
    if len(raw) > MAX_PASSWORD:
        raise Problem(400, "That password is too long.")
    return raw


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def password_ok(account, raw: str) -> bool:
    if not account or not account["password_hash"]:
        return False
    return bcrypt.checkpw(raw.encode(), account["password_hash"].encode())


def confirm_ok(account, raw: str) -> bool:
    """The password a sensitive change asks for. An account made through
    Google or GitHub has none until it sets one; its session is the proof."""
    return password_ok(account, raw or "") if account["password_hash"] else True


# --- lookup -------------------------------------------------------------------

def by_id(conn, account_id: str):
    return conn.execute("SELECT * FROM accounts WHERE id = ? AND deleted_at IS NULL", (account_id,)).fetchone()


def by_email(conn, email: str):
    """The address as typed, else any alias of the same inbox — one account
    holds the whole inbox (``email_canon``), so every alias names it. Mail
    still goes to the address the account stores. Two accounts predating the
    rule may share a canonical form; the older one answers."""
    row = conn.execute("SELECT * FROM accounts WHERE email = ? AND deleted_at IS NULL", (email,)).fetchone()
    if row:
        return row
    return conn.execute("SELECT * FROM accounts WHERE email_canon = ? AND email_canon != '' AND deleted_at IS NULL "
                        "ORDER BY created_at LIMIT 1", (email_canon(email),)).fetchone()


def by_username(conn, username: str):
    return conn.execute("SELECT * FROM accounts WHERE username = ? AND deleted_at IS NULL", (username,)).fetchone()


def by_login(conn, login: str):
    """The account a sign-in form names: an e-mail address or a username."""
    login = (login or "").strip().lower()
    if "@" in login:
        return by_email(conn, login)
    return by_username(conn, login)


def login_bucket(login: str) -> str:
    """The name a per-account rate limit counts, for the login the form sent
    or the address a reset names. An address is cut to the inbox it reaches
    (``email_canon``): ``by_email`` answers to every alias of one inbox, so
    the aliases must spend one allowance, not one each. A username is used
    as typed, lowercased. ``/api/login`` and ``/authorize/login`` build the
    same key from it and so share their window."""
    login = (login or "").strip().lower()[:254]
    return email_canon(login) if "@" in login else login


def email_taken(conn, email: str, exclude_id: str = "") -> bool:
    """Whether an account already has this address or another alias of the
    same inbox. A deleted account keeps its address for the grace period,
    so it counts too (``create``'s message says so)."""
    return conn.execute(
        "SELECT 1 FROM accounts WHERE (email = ? OR (email_canon != '' AND email_canon = ?)) AND id != ?",
        (email, email_canon(email), exclude_id)).fetchone() is not None


def public(account, conn=None) -> dict:
    """What the account owner (and ``/api/me``) sees. ``plan_source`` says
    where the effective plan comes from (``stripe`` / ``granted`` / ``free``);
    ``renews_at`` and ``cancel_at`` are the paid period's end when the
    subscription renews or ends then. A paid plan reads the subscription
    row, through ``conn`` or a short connection of its own."""
    out = {
        "id": account["id"], "username": account["username"], "email": account["email"],
        "email_verified": bool(account["email_verified_at"]), "display_name": account["display_name"],
        "plan": account["plan"], "granted_plan": account["granted_plan"], "plan_source": "free",
        "renews_at": None, "cancel_at": None,
        "is_admin": bool(account["is_admin"]), "created_at": account["created_at"],
        "has_password": bool(account["password_hash"]),
        "app_signed_in": bool(account["app_signed_in_at"]),
    }
    if account["plan"] == "free":  # nothing pays for or grants more: the subscription adds nothing
        return out
    if conn is None:
        with closing(db.connect()) as own:
            sub = subscription(own, account["id"])
    else:
        sub = subscription(conn, account["id"])
    out.update(plan_terms(account, sub))
    return out


# --- the effective plan -------------------------------------------------------

# Stripe's subscription statuses that pay for the plan. ``past_due`` counts
# too, but only through the grace period (``config.GRACE_DAYS``).
LIVE_STATUSES = ("active", "trialing")


def _rank(plan: str) -> int:
    return config.PLAN_RANK.get(plan, 0)


def subscription(conn, account_id: str):
    """The account's ``subscriptions`` row, or None."""
    return conn.execute("SELECT * FROM subscriptions WHERE account_id = ?", (account_id,)).fetchone()


def in_grace(sub) -> bool:
    """A failed payment still inside the grace period."""
    return bool(sub and sub["status"] == "past_due" and sub["past_due_since"]
                and sub["past_due_since"] > after(-config.GRACE_DAYS * 86400))


def billed_plan(sub) -> str:
    """The plan the subscription row pays for right now: its plan while the
    status is live or a failed payment is in grace, else ``free``."""
    if not sub or sub["plan"] not in config.PLANS:
        return "free"
    return sub["plan"] if sub["status"] in LIVE_STATUSES or in_grace(sub) else "free"


def plan_terms(account, sub) -> dict:
    """``plan_source``, ``renews_at`` and ``cancel_at`` for ``public``."""
    billed = billed_plan(sub)
    if billed != "free" and _rank(billed) >= _rank(account["granted_plan"]):
        source = "stripe"
    else:
        source = "granted" if account["granted_plan"] != "free" else "free"
    ends = billed != "free" and bool(sub["cancel_at_period_end"])
    return {"plan_source": source,
            "renews_at": sub["current_period_end"] if billed != "free" and not ends else None,
            "cancel_at": sub["current_period_end"] if ends else None}


def recompute_plan(conn, account_id: str, actor: str = "system", source: str = "", notify: bool = False) -> str:
    """Set ``plan`` to the higher of the granted and the billed plan, and
    return it. A change is audited with its ``source`` (``stripe`` /
    ``admin`` / ``invite``). The hosted server hears of it
    (``hosted.plan_changed``) when the plan changed, and always with
    ``notify``: billing passes it after every subscription write, so a grace
    period or a cancel reaches the server before the plan itself moves."""
    from . import hosted  # imported here: hosted.py may import this module
    account = conn.execute("SELECT plan, granted_plan FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not account:
        return "free"
    billed = billed_plan(subscription(conn, account_id))
    granted = account["granted_plan"] if account["granted_plan"] in config.PLANS else "free"
    plan = billed if _rank(billed) > _rank(granted) else granted
    changed = plan != account["plan"]
    if changed:
        conn.execute("UPDATE accounts SET plan = ? WHERE id = ?", (plan, account_id))
        audit(conn, "account.plan", account_id, actor,
              f"{account['plan']} -> {plan} source={source or 'system'} granted={granted} billed={billed}")
    if changed or notify:
        hosted.plan_changed(conn, account_id)
    return plan


# --- creation -----------------------------------------------------------------

def take_invite(conn, code: str) -> str:
    """Consume one use of an invite code; returns the plan it grants
    (``create`` stores it as the account's ``granted_plan``). In
    ``open`` mode a missing code is fine; in ``invite`` mode it is required;
    in ``closed`` mode registration is refused before this is reached."""
    code = (code or "").strip()
    if not code:
        if settings.registration() == "open":
            return "free"
        raise Problem(403, "Registration needs an invite code right now.")
    row = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
    if not row or row["uses_left"] <= 0:
        raise Problem(403, "That invite code is not valid.")
    conn.execute("UPDATE invites SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    return row["plan"]


def make_invite(conn, *, uses: int, plan: str, note: str, created_by: str) -> dict:
    """A new invite code; returns its row."""
    if plan not in config.PLANS:
        raise Problem(400, "unknown plan")
    if not 1 <= uses <= 10000:
        raise Problem(400, "uses must be 1..10000")
    code = new_token(9)
    conn.execute("INSERT INTO invites (code, uses_left, plan, created_by, created_at, note) VALUES (?, ?, ?, ?, ?, ?)",
                 (code, uses, plan, created_by, now(), note[:200]))
    audit(conn, "invite.create", actor=created_by, detail=f"{code} uses={uses} plan={plan}")
    return dict(conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone())


def create(conn, *, email: str, username: str, password: str | None, plan: str = "free",
           display_name: str = "", verified: bool = False, actor: str = "") -> dict:
    """Insert an account. Uniqueness is checked here so the API can answer
    with a message; the UNIQUE constraints are the backstop. A deleted
    account keeps its e-mail and username for the grace period, so those are
    unavailable too (the message says so)."""
    if email_taken(conn, email):
        raise Problem(409, "There is already an account with that e-mail address.")
    if conn.execute("SELECT 1 FROM accounts WHERE username = ?", (username,)).fetchone():
        raise Problem(409, "That username is taken.")
    if plan not in config.PLANS:
        raise Problem(400, "unknown plan")
    account_id = new_id()
    ts = now()
    # The plan an invite (or the operator) gives is a grant; the effective
    # plan follows from it.
    conn.execute(
        "INSERT INTO accounts (id, username, email, email_canon, email_verified_at, password_hash, display_name, "
        "plan, granted_plan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'free', ?, ?)",
        (account_id, username, email, email_canon(email), ts if verified else None,
         hash_password(password) if password else None, display_name[:100], plan, ts))
    audit(conn, "account.create", account_id, actor or account_id, f"username={username} plan={plan}")
    if plan != "free":
        recompute_plan(conn, account_id, actor or account_id, "admin" if actor else "invite")
    return by_id(conn, account_id)


# --- e-mail links -------------------------------------------------------------

def issue_email_token(conn, account_id: str, kind: str, ttl: int, payload: str = "") -> str:
    """One pending link per (account, kind): issuing a new one voids the
    previous, so a resend never leaves two valid links around."""
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE account_id = ? AND kind = ? AND used_at IS NULL",
                 (now(), account_id, kind))
    token = new_token(32)
    conn.execute("INSERT INTO email_tokens (token_hash, kind, account_id, payload, created_at, expires_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (token_hash(token), kind, account_id, payload, now(), after(ttl)))
    return token


def consume_email_token(conn, token: str, kind: str):
    """The (account, payload) a live link names, marking it used; None when
    unknown, expired, used or of another kind."""
    row = conn.execute("SELECT * FROM email_tokens WHERE token_hash = ?", (token_hash(token or ""),)).fetchone()
    if not row or row["kind"] != kind or row["used_at"] or row["expires_at"] <= now():
        return None
    account = by_id(conn, row["account_id"])
    if not account:
        return None
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE token_hash = ?", (now(), row["token_hash"]))
    return account, row["payload"]


def link(path: str, token: str) -> str:
    return f"{config.PUBLIC_URL}{path}?token={token}"


def _hi(account) -> str:
    return f"Hi {account['display_name'] or account['username']},"


def verify_mail(account, token: str) -> tuple[str, str, str]:
    return ("Confirm your Gamma Cloud e-mail address", *mail.compose(
        _hi(account),
        ["Welcome to Gamma Cloud. Confirm this address to finish setting up your account "
         f"@{account['username']}."],
        ("Confirm e-mail address", link("/verify", token)),
        "The link works for one day. If you did not create an account, ignore this mail."))


def reset_mail(account, token: str) -> tuple[str, str, str]:
    return ("Reset your Gamma Cloud password", *mail.compose(
        _hi(account),
        ["Someone asked to reset the password of your Gamma Cloud account. Set a new one here."],
        ("Set a new password", link("/reset/confirm", token)),
        "The link works for one hour. If you did not ask for this, ignore this mail; your password "
        "is unchanged."))


def change_email_mail(account, new_email: str, token: str) -> tuple[str, str, str]:
    return ("Confirm your new Gamma Cloud e-mail address", *mail.compose(
        _hi(account),
        [f"Confirm {new_email} as the address of your Gamma Cloud account."],
        ("Confirm new address", link("/email/confirm", token)),
        "The link works for one day. Until you confirm, your account keeps its current address."))


def email_changed_notice(account, new_email: str) -> tuple[str, str, str]:
    return ("Your Gamma Cloud e-mail address changed", *mail.compose(
        _hi(account),
        [f"The e-mail address of your Gamma Cloud account is now {new_email}."],
        None,
        "If that was not you, reply to this mail."))


# --- changes ------------------------------------------------------------------

def mark_verified(conn, account_id: str) -> None:
    """Every path that confirms an address comes here (the mail link, a
    reset, a Google/GitHub sign-in on the address, the admin). The first
    confirmation tells the hosted side (``hosted.plan_changed``): a hosted
    server waits for a confirmed e-mail, so a paid plan granted at
    registration (an invite) gets its server once the link is clicked."""
    from . import hosted  # imported here: hosted.py imports this module
    first = conn.execute("UPDATE accounts SET email_verified_at = ? WHERE id = ? AND email_verified_at IS NULL",
                         (now(), account_id)).rowcount
    audit(conn, "account.verify", account_id, account_id)
    if first:
        hosted.plan_changed(conn, account_id)


def set_password(conn, account_id: str, password: str, actor: str = "") -> None:
    """Also signs the account out everywhere: portal sessions and every
    OIDC grant (and their access tokens) go."""
    conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(password), account_id))
    revoke_everything(conn, account_id)
    audit(conn, "account.password", account_id, actor or account_id)


def set_email(conn, account_id: str, email: str, actor: str = "") -> None:
    if email_taken(conn, email, account_id):
        raise Problem(409, "There is already an account with that e-mail address.")
    conn.execute("UPDATE accounts SET email = ?, email_canon = ?, email_verified_at = ? WHERE id = ?",
                 (email, email_canon(email), now(), account_id))
    audit(conn, "account.email", account_id, actor or account_id, email)


def set_username(conn, account_id: str, username: str, actor: str = "") -> None:
    """Rename. The account id (the OIDC ``sub``) is what Gamma servers key
    on, so a rename changes nothing there until the next sign-in refreshes
    the claims; a deleted account's name stays taken through its grace
    period like at registration. Refused while the account has a hosted
    server that is not deleted: its hostname, public URL and redirect URI
    were fixed from the username at provisioning (a rename job is later
    work, docs/research/cloud-plans.md "Lifecycle")."""
    username = norm_username(username)
    current = conn.execute("SELECT username FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if current and current["username"] == username:
        return
    if conn.execute("SELECT 1 FROM hosted_servers WHERE account_id = ? AND state != 'deleted' AND deleted_at IS NULL",
                    (account_id,)).fetchone():
        raise Problem(409, "Your hosted server is named after your username; contact support to rename.")
    if conn.execute("SELECT 1 FROM accounts WHERE username = ? AND id != ?", (username, account_id)).fetchone():
        raise Problem(409, "That username is taken.")
    conn.execute("UPDATE accounts SET username = ? WHERE id = ?", (username, account_id))
    audit(conn, "account.username", account_id, actor or account_id, username)


def set_plan(conn, account_id: str, plan: str, actor: str) -> None:
    """The courtesy grant (the Admin page's plan select, ``manage.py
    set-plan``). A subscription above it keeps the higher plan, and the
    grant outlives the subscription."""
    if plan not in config.PLANS:
        raise Problem(400, "unknown plan")
    conn.execute("UPDATE accounts SET granted_plan = ? WHERE id = ?", (plan, account_id))
    audit(conn, "account.grant", account_id, actor, plan)
    recompute_plan(conn, account_id, actor, "admin")


def set_admin(conn, account_id: str, is_admin: bool, actor: str) -> None:
    conn.execute("UPDATE accounts SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, account_id))
    audit(conn, "account.admin", account_id, actor, "on" if is_admin else "off")


def set_display_name(conn, account_id: str, name: str) -> None:
    conn.execute("UPDATE accounts SET display_name = ? WHERE id = ?", ((name or "").strip()[:100], account_id))


def revoke_everything(conn, account_id: str) -> None:
    """Every browser, every grant with its access tokens (and the servers
    that registered with one), and every code minted but not yet exchanged
    (it would become a new grant)."""
    conn.execute("DELETE FROM portal_sessions WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM oauth_codes WHERE account_id = ?", (account_id,))
    conn.execute("UPDATE grants SET revoked_at = ?, refresh_hash = NULL WHERE account_id = ? AND revoked_at IS NULL",
                 (now(), account_id))
    conn.execute("DELETE FROM access_tokens WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM servers_linked WHERE account_id = ? AND grant_id != ''", (account_id,))


def delete(conn, account_id: str, actor: str = "") -> None:
    """Soft delete: the row keeps its username and e-mail through the grace
    period (``manage.py purge-deleted`` removes it), nothing can sign in as
    it any more (its Google/GitHub links, preference profile and linked
    servers go at once), and its servers' teardown is the provisioner's job
    (v1)."""
    revoke_everything(conn, account_id)
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE account_id = ? AND used_at IS NULL", (now(), account_id))
    conn.execute("UPDATE accounts SET deleted_at = ?, password_hash = NULL WHERE id = ?", (now(), account_id))
    for table in ("identities", "prefs", "servers_linked"):
        conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
    audit(conn, "account.delete", account_id, actor or account_id)


def by_id_deleted(conn, account_id: str):
    """A soft-deleted account still in its grace period, or None."""
    return conn.execute("SELECT * FROM accounts WHERE id = ? AND deleted_at IS NOT NULL", (account_id,)).fetchone()


def restore(conn, account_id: str, actor: str) -> None:
    """Undo a soft delete within the grace period. The username and e-mail
    were reserved, so nothing can have taken them. What the delete dropped
    stays dropped: the person signs back in through a password reset, or
    through Google/GitHub on the same e-mail, which links again."""
    conn.execute("UPDATE accounts SET deleted_at = NULL WHERE id = ?", (account_id,))
    audit(conn, "account.restore", account_id, actor)


def purge(conn, account_id: str, actor: str = "system") -> None:
    """Remove an account and every row that references it (the audit log
    keeps its history); its username and e-mail are free again. A live
    hosted server is deleted first (``hosted.purge_account``). The
    subscription copy goes too: only a deleted account is purged, and the
    deletion already cancelled the subscription at Stripe
    (``billing.cancel_for_deletion``); ``billing_events`` keeps the id."""
    from . import hosted  # imported here: hosted.py imports this module
    hosted.purge_account(conn, account_id, actor)
    for table in ("subscriptions", "identities", "portal_sessions", "email_tokens", "grants", "access_tokens",
                  "prefs", "servers_linked"):
        conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    audit(conn, "account.purge", account_id, actor)


def purge_deleted(conn, older_than_days: int) -> int:
    """Purge accounts deleted more than N days ago. Returns the count."""
    cutoff = after(-older_than_days * 86400)
    rows = conn.execute("SELECT id FROM accounts WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff,)).fetchall()
    for row in rows:
        purge(conn, row["id"])
    return len(rows)

import os
import tempfile
from pathlib import Path

_DATA = Path(tempfile.mkdtemp(prefix="gammacloud-test-"))
os.environ["GAMMA_CLOUD_DATA_DIR"] = str(_DATA)
os.environ["GAMMA_CLOUD_MAIL"] = "memory"
os.environ["GAMMA_CLOUD_PUBLIC_URL"] = "http://testserver"
# The sign-up gate lives in cloud.db, not the environment (gammacloud/settings.py):
# a fresh database takes the defaults, and set_setting() below changes one.
for _name in ("GAMMA_CLOUD_REGISTRATION", "GAMMA_CLOUD_TURNSTILE_SITEKEY",
              "GAMMA_CLOUD_TURNSTILE_SECRET", "GAMMA_CLOUD_BLOCKED_EMAIL_DOMAINS"):
    os.environ.pop(_name, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gammacloud import db, mail, ratelimit, settings  # noqa: E402
from gammacloud.app import create_app  # noqa: E402
from gammacloud.accounts import make_invite  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    for f in _DATA.glob("cloud.db*"):
        f.unlink()
    mail.outbox.clear()
    ratelimit.clear()
    settings.invalidate()  # the cache belongs to the database that was just dropped
    yield


@pytest.fixture
def client():
    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c


def steps_after(version):
    """The upgrade steps a cloud.db at ``version`` still needs, in order. The
    upgrade tests assert against this rather than a written-out list, so
    adding a step does not mean editing every one of them."""
    return [name for v, name, _ in db.STEPS if v > version]


def set_setting(key, value):
    """Change a server setting the way the Admin page does."""
    from contextlib import closing
    with closing(db.connect()) as conn:
        settings.update(conn, {key: value}, actor="test")
        conn.commit()
    settings.invalidate()


def invite(uses=1, plan="free"):
    from contextlib import closing
    with closing(db.connect()) as conn:
        row = make_invite(conn, uses=uses, plan=plan, note="", created_by="test")
        conn.commit()
    return row["code"]


def register(client, username="alice", email=None, password="correct horse battery", code=None):
    r = client.post("/api/register", json={"email": email or f"{username}@example.org", "username": username,
                                           "password": password, "invite": code or invite()})
    assert r.status_code == 201, r.text
    return r.json()["account"]


def last_link(path):
    """The token of the newest mail whose link path matches."""
    for m in reversed(mail.outbox):
        for word in m["body"].split():
            if word.startswith(f"http://testserver{path}?token="):
                return word.split("token=", 1)[1]
    raise AssertionError(f"no mail with {path} link; outbox={mail.outbox}")


def verify(client, username="alice"):
    r = client.post("/api/verify", json={"token": last_link("/verify")})
    assert r.status_code == 200, r.text
    assert r.json()["account"]["email_verified"] is True


def make_admin(username):
    from contextlib import closing
    from gammacloud import accounts
    with closing(db.connect()) as conn:
        account = accounts.by_username(conn, username)
        accounts.set_admin(conn, account["id"], True, "test")
        conn.commit()

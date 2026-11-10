"""Reports (gamma/reports.py, routers/reports.py): a comment, a shared or
published page and an AI reply reported, with what is reported read where
the reporter reads it; the refusals (a kind or reason it does not know, what
the reporter cannot read, the AI chat without an account, the rate limit);
the admins' list, resolve, reopen, delete and notice; and on a server with
Gamma Cloud sign-in, the report sent on to the account server (the fake one
of tests/test_cloud_auth.py) with the reporter's token, the server's client
secret or neither, again by the hourly round when it failed, and the
round's purge."""

from datetime import datetime, timedelta, timezone

import pytest
from conftest import account_of, drop_user, login, make_page, make_user
from test_cloud_auth import cloud  # noqa: F401  (a fixture)

from gamma import cloud_auth, notices, reports
from gamma.db import connect_users_db, format_stamp


@pytest.fixture(autouse=True, scope="module")
def no_reports():
    """The module counts every report there is: it starts and ends with none."""
    def clear():
        with connect_users_db() as conn:
            conn.execute("DELETE FROM content_reports")
            conn.commit()
    clear()
    yield
    clear()


@pytest.fixture(scope="module")
def own():
    make_user("rp_own", "rp-own-pw1")
    return login("rp_own", "rp-own-pw1")


@pytest.fixture(scope="module")
def bob():
    make_user("rp_bob", "rp-bob-pw1")
    return login("rp_bob", "rp-bob-pw1")


@pytest.fixture(scope="module")
def boss():
    make_user("rp_boss", "rp-boss-pw1", is_admin=1)
    yield login("rp_boss", "rp-boss-pw1")
    drop_user("rp_boss")  # test_admin_users knows every admin there is


@pytest.fixture(scope="module")
def shared(own):
    """rp_own's page shared with anyone by link, a comment of theirs on it:
    (page id, share token, comment id)."""
    page = make_page(own, "Reading notes")["id"]
    r = own.post(f"/api/share/{page}", json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = own.post(f"/api/pages/{page}/comments", json={"block_id": page, "body": "Everyone here is an idiot"})
    assert r.status_code == 201, r.text
    return page, token, r.json()["comment"]["id"]


def _stored(report_id):
    with connect_users_db() as conn:
        conn.row_factory = None
        row = conn.execute("SELECT kind, reason, details, content, author, title, page_id, comment_id, link, "
                           "reporter_id, reporter, state, sent_at, send_error FROM content_reports WHERE id = ?",
                           (report_id,)).fetchone()
    keys = ("kind", "reason", "details", "content", "author", "title", "page_id", "comment_id", "link",
            "reporter_id", "reporter", "state", "sent_at", "send_error")
    return dict(zip(keys, row)) if row else None


def _file(who, params=None, **body):
    return who.post("/api/reports", json={"reason": "harassment", **body}, params=params)


def test_a_comment_is_reported_as_the_server_reads_it(bob, shared):
    page, token, comment = shared
    r = _file(bob, {"share": token}, kind="comment", comment_id=comment, details="Again\r\nand again",
              content="not what it says")
    assert r.status_code == 201, r.text
    assert r.json()["forwarded"] is False      # no Gamma Cloud sign-in here
    row = _stored(r.json()["id"])
    assert row == {"kind": "comment", "reason": "harassment", "details": "Again\nand again",
                   "content": "Everyone here is an idiot", "author": "rp_own", "title": "Reading notes",
                   "page_id": page, "comment_id": comment, "link": "", "reporter_id": account_of("rp_bob"),
                   "reporter": "rp_bob", "state": "open", "sent_at": "", "send_error": ""}
    # what the reporter cannot read, they cannot report
    assert _file(bob, kind="comment", comment_id=comment).status_code == 404   # not in bob's own library
    assert _file(bob, {"share": token}, kind="comment", comment_id="nope").status_code == 404


def test_a_link_visitor_reports_the_shared_or_published_page(anon, shared):
    page, token, _ = shared
    r = _file(anon, {"share": token}, kind="page", page_id=page, reason="spam",
              link="https://rp-own-pages.example.org/reading-notes-" + page)
    assert r.status_code == 201, r.text
    row = _stored(r.json()["id"])
    assert (row["kind"], row["author"], row["title"], row["reporter_id"], row["reporter"]) == (
        "page", "rp_own", "Reading notes", "", "")
    assert row["link"] == "https://rp-own-pages.example.org/reading-notes-" + page
    # a share view's own address would hand on its token: it is dropped
    r = _file(anon, {"share": token}, kind="page", page_id=page, link=f"http://testserver/?share={token}")
    assert _stored(r.json()["id"])["link"] == ""
    assert _file(anon, kind="page", page_id=page).status_code == 401
    assert _file(anon, {"share": token}, kind="page", page_id="other").status_code == 404


def test_an_ai_reply_is_reported_by_its_reader(anon, bob, shared):
    r = _file(bob, kind="ai", reason="harmful", content="Here is how to …", model="gpt-x")
    assert r.status_code == 201, r.text
    row = _stored(r.json()["id"])
    assert (row["kind"], row["content"], row["author"], row["page_id"]) == ("ai", "Here is how to …", "gpt-x", "")
    assert _file(anon, kind="ai", content="text").status_code == 401
    assert _file(anon, {"share": "nope.nope"}, kind="ai", content="text").status_code in (401, 403)
    assert _file(bob, kind="ai", content="  ").status_code == 400
    # a saved chat a share link shows: its visitor may report a reply in it
    r = _file(anon, {"share": shared[1]}, kind="ai", content="A saved answer", model="")
    assert r.status_code == 201 and _stored(r.json()["id"])["reporter"] == ""


def test_what_a_report_may_not_be(bob, shared):
    page, token, comment = shared
    assert _file(bob, kind="user", page_id=page).status_code == 400
    assert _file(bob, kind="page", page_id=page, reason="rude").status_code == 400
    assert _file(bob, {"share": token}, kind="comment").status_code == 400      # names no comment
    for _ in range(10):  # the refusals above counted none
        assert _file(bob, kind="ai", content="x").status_code == 201
    r = _file(bob, kind="ai", content="x")
    assert r.status_code == 429


def test_admins_review_the_reports(own, boss, shared):
    page, token, comment = shared
    from gamma import ratelimit
    ratelimit._buckets.clear()
    r = _file(own, kind="ai", content="reported by the owner", reason="other")
    rid = r.json()["id"]
    assert own.get("/api/admin/reports").status_code == 403
    listed = boss.get("/api/admin/reports").json()
    assert listed["forwards"] is False and listed["open"] >= 1
    assert rid in [x["id"] for x in listed["reports"]]
    # an admin is told; the notice names the newest
    ids = [n["id"] for n in boss.get("/api/notices").json()["notices"]]
    assert "reports" in ids
    found = notices.reports_waiting(account_of("rp_boss"))
    assert found.pane == "server" and found.params["n"] == listed["open"]
    r = boss.patch(f"/api/admin/reports/{rid}", json={"state": "resolved"})
    assert r.status_code == 200 and r.json()["report"]["state"] == "resolved"
    assert r.json()["report"]["resolved_by"] == account_of("rp_boss")
    assert rid not in [x["id"] for x in boss.get("/api/admin/reports?state=open").json()["reports"]]
    assert rid in [x["id"] for x in boss.get("/api/admin/reports?state=resolved").json()["reports"]]
    assert boss.patch(f"/api/admin/reports/{rid}", json={"state": "open"}).json()["report"]["resolved_at"] == ""
    assert boss.patch(f"/api/admin/reports/{rid}", json={"state": "gone"}).status_code == 400
    assert boss.get("/api/admin/reports?state=nope").status_code == 400
    assert boss.delete(f"/api/admin/reports/{rid}").status_code == 200
    assert boss.delete(f"/api/admin/reports/{rid}").status_code == 404
    # an admin's own reports are no notice to that admin
    with connect_users_db() as conn:
        conn.execute("UPDATE content_reports SET state = 'resolved'")
        conn.commit()
    r = _file(boss, kind="ai", content="mine")
    assert notices.reports_waiting(account_of("rp_boss")) is None
    assert notices.reports_waiting(account_of("rp_own")).params["n"] == 1


def test_a_report_goes_on_to_gamma_cloud(cloud, own, bob, shared, monkeypatch):  # noqa: F811
    from gamma import ratelimit
    ratelimit._buckets.clear()
    monkeypatch.setattr(reports, "_background", lambda fn: fn())
    page, token, comment = shared
    # no linked account, a public client: unsigned
    r = _file(bob, {"share": token}, kind="comment", comment_id=comment)
    assert r.status_code == 201 and r.json()["forwarded"] is True
    [sent] = cloud.filed
    assert (sent["report_id"], sent["kind"], sent["content"], sent["author"], sent["reporter"], sent["auth"]) == (
        r.json()["id"], "comment", "Everyone here is an idiot", "rp_own", "rp_bob", "")
    assert _stored(r.json()["id"])["sent_at"]
    # a confidential client signs it with its secret
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", "gc_lab")
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", "lab-secret")
    _file(bob, kind="ai", content="bad reply")
    import base64
    assert cloud.filed[-1]["auth"] == "Basic"
    assert base64.b64decode(cloud.filed[-1]["basic"]).decode() == "gc_lab:lab-secret"
    # a reporter whose Gamma Cloud account is linked here sends their own token
    cloud.access["at-bob"] = "sub-rp_bob"
    monkeypatch.setattr(cloud_auth, "grant_of", lambda uid: ("sub-rp_bob", "rt") if uid == account_of("rp_bob")
                        else ("", ""))
    tokens = {"now": "at-bob"}
    monkeypatch.setattr(cloud_auth, "access_token_for", lambda uid, fresh=False: tokens["now"])
    _file(bob, kind="ai", content="worse reply")
    assert (cloud.filed[-1]["auth"], cloud.filed[-1]["by"]) == ("Bearer", "sub-rp_bob")
    # a token the account server no longer takes: sent again as the server
    tokens["now"] = "at-dead"
    _file(bob, kind="ai", content="worst reply")
    assert cloud.filed[-1]["auth"] == "Basic" and cloud.filed[-1]["content"] == "worst reply"


def test_the_hourly_round_sends_again_and_purges(cloud, bob, monkeypatch):  # noqa: F811
    from gamma import ratelimit
    ratelimit._buckets.clear()
    monkeypatch.setattr(reports, "_background", lambda fn: fn())
    cloud.offline = True
    rid = _file(bob, kind="ai", content="sent later").json()["id"]
    row = _stored(rid)
    assert row["sent_at"] == "" and "offline" in row["send_error"]
    with connect_users_db() as conn:
        old = format_stamp(datetime.now(timezone.utc) - timedelta(days=reports.KEEP_DAYS + 1))
        conn.execute("UPDATE content_reports SET created_at = ? WHERE id != ?", (old, rid))
        conn.commit()
    cloud.offline = False
    done = reports.tick()
    assert done["sent"] >= 1 and done["dropped"] >= 1
    assert _stored(rid)["sent_at"] and cloud.filed[-1]["report_id"] == rid
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_reports").fetchone()[0] == 1

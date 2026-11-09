"""Reports a Gamma server forwards (reports.py, routers/reports.py): who
sent one (the reporter's token, the server's client secret, or neither),
the fields kept and refused, a retry stored once, the per-IP limit of an
unsigned call, the alert and its mail, the Admin tab's list, resolve,
reopen and delete, the purge, a purged account's reports, the terms on the
sign-up page and the step-15 upgrade."""

import base64
import sqlite3
from contextlib import closing

import manage
from conftest import make_admin, register, steps_after, verify
from fastapi.testclient import TestClient
from test_profile import SERVER, bearer, lab_client, lab_tokens

from gammacloud import accounts, config, db, mail, pages, reports


def report(**over):
    return {"report_id": "rep_" + "a" * 12, "server_url": SERVER, "server_name": "The lab", "kind": "comment",
            "reason": "harassment", "details": "Keeps\r\nposting this", "content": "You are all idiots",
            "author": "mallory", "title": "Reading notes", "page_id": "pg1", "comment_id": "cm1",
            "reporter": "bob", **over}


def rows():
    with closing(db.connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_at")]


def admin(client):
    """Alice, a confirmed admin signed in to the portal: the operator."""
    register(client)
    verify(client)
    make_admin("alice")


def basic(client_id, secret):
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def test_an_unsigned_report_is_kept_as_the_server_sent_it(client):
    r = client.post("/api/reports", json=report(link="https://alice-pages.gammapdf.com/notes-pg1"))
    assert r.status_code == 201, r.text
    [row] = rows()
    assert r.json() == {"ok": True, "id": row["id"]}
    assert (row["kind"], row["reason"], row["server_url"], row["server_name"]) == ("comment", "harassment", SERVER,
                                                                                   "The lab")
    assert row["details"] == "Keeps\nposting this" and row["content"] == "You are all idiots"
    assert (row["author"], row["title"], row["page_id"], row["comment_id"], row["reporter"]) == (
        "mallory", "Reading notes", "pg1", "cm1", "bob")
    assert row["link"] == "https://alice-pages.gammapdf.com/notes-pg1"
    assert row["account_id"] == "" and row["client_id"] == "" and row["state"] == "open"
    # a retry of the same report stores nothing new
    again = client.post("/api/reports", json=report(details="changed"))
    assert again.json()["id"] == row["id"] and len(rows()) == 1 and rows()[0]["details"] == "Keeps\nposting this"
    # the same id from another server is another report
    assert client.post("/api/reports", json=report(server_url="https://other.example.org")).status_code == 201
    assert len(rows()) == 2


def test_the_fields_are_checked_and_cut(client):
    for over, word in (({"kind": "user"}, "kind"), ({"reason": "rude"}, "reason"), ({"report_id": ""}, "report_id"),
                       ({"report_id": "a b"}, "report_id"), ({"comment_id": "x" * 65}, "comment_id"),
                       ({"server_url": "http://lab.example.org"}, "server address")):
        r = client.post("/api/reports", json=report(**over))
        assert r.status_code == 400 and word in r.json()["detail"], (over, r.text)
    assert rows() == []
    r = client.post("/api/reports", json=report(content="x" * 9000, author="a\x00b " * 100, link="javascript:alert(1)",
                                                server_url="", server_name=""))
    assert r.status_code == 201
    [row] = rows()
    assert len(row["content"]) == reports.MAX_CONTENT and len(row["author"]) == reports.MAX_NAME
    assert "\x00" not in row["author"] and row["link"] == "" and row["server_url"] == ""


def test_a_token_names_the_reporter_and_a_secret_the_server(client):
    register(client)
    verify(client)
    client_id, secret = lab_client()
    tokens = lab_tokens(client, client_id, secret)
    assert client.post("/api/reports", headers=bearer(tokens), json=report(report_id="by-token")).status_code == 201
    assert client.post("/api/reports", headers=basic(client_id, secret), json=report(report_id="by-secret")).status_code == 201
    by = {r["report_id"]: r for r in rows()}
    with closing(db.connect()) as conn:
        alice_id = accounts.by_username(conn, "alice")["id"]
    assert (by["by-token"]["account_id"], by["by-token"]["client_id"]) == (alice_id, client_id)
    assert (by["by-secret"]["account_id"], by["by-secret"]["client_id"]) == ("", client_id)
    # a token or a secret that fails is refused, never taken for an unsigned call
    assert client.post("/api/reports", headers={"Authorization": "Bearer nope"}, json=report()).status_code == 401
    assert client.post("/api/reports", headers=basic(client_id, "wrong"), json=report()).status_code == 401
    assert client.post("/api/reports", headers={"Authorization": "Basic !!"}, json=report()).status_code == 401
    # the desktop client has no secret to prove anything with
    r = client.post("/api/reports", headers=basic(config.DESKTOP_CLIENT_ID, ""), json=report(report_id="desktop"))
    assert r.status_code == 201 and {r["report_id"]: r for r in rows()}["desktop"]["client_id"] == ""


def test_unsigned_reports_are_limited_per_address(client):
    for n in range(20):
        assert client.post("/api/reports", json=report(report_id=f"r{n}")).status_code == 201
    r = client.post("/api/reports", json=report(report_id="r20"))
    assert r.status_code == 429 and "Retry-After" in r.headers
    # a server's own secret has an allowance of its own
    client_id, secret = lab_client()
    assert client.post("/api/reports", headers=basic(client_id, secret), json=report(report_id="r21")).status_code == 201


def test_a_report_is_an_alert_mailed_to_the_operator(client):
    admin(client)
    mail.outbox.clear()
    client.post("/api/reports", json=report(kind="ai", reason="harmful", content="Here is how to …"))
    [m] = mail.outbox
    assert m["to"] == "alice@example.org"
    assert m["subject"] == "Gamma Cloud: Report of an AI reply on The lab: harmful or dangerous content"
    assert "http://testserver/admin#reports" in m["body"]
    overview = client.get("/api/admin/overview").json()
    assert [a["link"] for a in overview["alerts"] if a["kind"] == "report"] == ["#reports"]
    # a retry mails nothing more
    client.post("/api/reports", json=report(kind="ai", reason="harmful"))
    assert len(mail.outbox) == 1


def test_the_admin_lists_resolves_reopens_and_deletes(client):
    client.post("/api/reports", json=report())
    assert client.get("/api/admin/reports").status_code == 401
    register(client, "bob")
    assert client.get("/api/admin/reports").status_code == 403
    with TestClient(client.app, base_url="http://testserver") as operator:
        admin(operator)
        d = operator.get("/api/admin/reports").json()
        assert d["open"] == 1 and [r["content"] for r in d["reports"]] == ["You are all idiots"]
        assert "ip" not in d["reports"][0]
        rid = d["reports"][0]["id"]
        r = operator.patch(f"/api/admin/reports/{rid}", json={"state": "resolved"})
        assert r.status_code == 200 and r.json()["report"]["state"] == "resolved"
        assert operator.get("/api/admin/reports").json() == {"reports": [], "open": 0}
        assert [r["id"] for r in operator.get("/api/admin/reports?state=resolved").json()["reports"]] == [rid]
        assert not [a for a in operator.get("/api/admin/overview").json()["alerts"] if a["kind"] == "report"]
        assert operator.patch(f"/api/admin/reports/{rid}", json={"state": "open"}).json()["report"]["resolved_at"] is None
        assert operator.patch(f"/api/admin/reports/{rid}", json={"state": "gone"}).status_code == 400
        assert operator.get("/api/admin/reports?state=nope").status_code == 400
        assert operator.delete(f"/api/admin/reports/{rid}").status_code == 200
        assert operator.delete(f"/api/admin/reports/{rid}").status_code == 404
        assert operator.patch(f"/api/admin/reports/{rid}", json={"state": "open"}).status_code == 404
        events = [a["event"] for a in operator.get("/api/admin/audit").json()["audit"]]
        assert {"report.resolve", "report.reopen", "report.delete"} <= set(events)
        assert "id=tab-reports" in operator.get("/admin").text


def test_the_cli_lists_and_resolves(client, capsys):
    client.post("/api/reports", json=report())
    [row] = rows()
    manage.main(["reports"])
    assert row["id"] in capsys.readouterr().out
    manage.main(["resolve-report", row["id"]])
    assert rows()[0]["state"] == "resolved" and rows()[0]["resolved_by"] == "cli"
    manage.main(["reports"])
    assert row["id"] not in capsys.readouterr().out.split("resolved")[-1]


def test_the_purge_drops_old_resolved_reports_and_purged_accounts(client):
    register(client)
    verify(client)
    tokens = lab_tokens(client, *lab_client())
    client.post("/api/reports", headers=bearer(tokens), json=report(report_id="old"))
    client.post("/api/reports", json=report(report_id="new"))
    with closing(db.connect()) as conn:
        alice_id = accounts.by_username(conn, "alice")["id"]
        conn.execute("UPDATE reports SET state = 'resolved', resolved_at = ? WHERE report_id = 'old'",
                     (db.after(-(reports.KEEP_DAYS + 1) * 86400),))
        reports.purge(conn)
        accounts.purge(conn, alice_id)
        conn.commit()
    assert [(r["report_id"], r["account_id"]) for r in rows()] == [("new", "")]
    with closing(db.connect()) as conn:
        conn.execute("UPDATE reports SET account_id = ?", (alice_id,))
        accounts.purge(conn, alice_id)
        conn.commit()
    assert rows()[0]["account_id"] == ""


def test_the_sign_up_page_asks_to_agree_to_the_terms(client):
    page = client.get("/register").text
    assert f"href='{pages.SITE}/terms/'>terms</a>" in page and f"href='{pages.SITE}/privacy/'" in page
    assert "By continuing you agree to the" in client.get("/login").text


def test_step_15_adds_the_reports(client):
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE reports")
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
    assert steps_after(14)[0] == "reports"
    assert db.ensure_current() == steps_after(14)
    assert db.ensure_current() == []
    with closing(sqlite3.connect(str(config.DB_PATH))) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        db._step_reports(conn)  # re-runnable
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'idx_reports_state'").fetchone()

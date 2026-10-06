"""What the operator is told (docs/dev/hosted.md "Alerts"): every kind of
problem found and resolved, the settle before a mail, one mail to the
right addresses, the switch, a problem that comes back, dismissal, the
Overview and the test alert."""

import json
from contextlib import closing

from conftest import make_admin, register, set_setting, verify
from test_fleet import bearer, finish, next_job
from test_hosted import backdate, hosting, make_account, make_host, server  # noqa: F401

from gammacloud import accounts, alerts, db, fleet, hosted, mail


def collect() -> dict:
    with closing(db.connect()) as conn:
        return {a["key"]: a for a in alerts.collect(conn)}


def sync() -> list[dict]:
    with closing(db.connect()) as conn:
        due = alerts.sync(conn)
        conn.commit()
    return due


def row(key):
    with closing(db.connect()) as conn:
        r = conn.execute("SELECT * FROM alerts WHERE key = ?", (key,)).fetchone()
        return dict(r) if r else None


def settle(key, minutes):
    """Move an alert's opening back in time."""
    with closing(db.connect()) as conn:
        conn.execute("UPDATE alerts SET first_at = ? WHERE key = ?", (db.after(-minutes * 60), key))
        conn.commit()


def beat(client, token, containers, **host):
    body = {"agent_version": "1", "memory_mb": 8192, "disk_mb": 500_000, "memory_used_mb": 1024, "disk_used_mb": 10_000,
            "containers": containers, **host}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200


def alert_mails():
    return [m for m in mail.outbox if m["subject"].startswith("Gamma Cloud:")]


def operator(client, name="operator"):
    """An admin signed in on ``client`` with a confirmed address."""
    me = register(client, name)
    verify(client)
    make_admin(name)
    return me


def other_admin(username, verified=True):
    with closing(db.connect()) as conn:
        a = accounts.create(conn, email=f"{username}@example.org", username=username, password=None, verified=verified)
        accounts.set_admin(conn, a["id"], True, "test")
        conn.commit()


# --- what is found ------------------------------------------------------------

def test_a_failed_job_a_stuck_server_and_a_container_down(client, hosting):
    _, token = make_host()
    alice = make_account("alice", "plus")
    sid = server(alice)["id"]
    job = next_job(client, token)
    finish(client, token, job["id"], "failed", {"error": "pull failed"})
    found = collect()[f"job:{job['id']}"]
    assert found["text"] == "create job for alice failed: pull failed"
    assert (found["link"], found["settle"]) == ("#servers", 0)
    with closing(db.connect()) as conn:
        fleet.retry(conn, job["id"], "test")
        conn.commit()
    assert f"job:{job['id']}" not in collect()                 # retried: no longer failed

    backdate(alice, "state_changed_at", 2 / 24)
    assert collect()[f"stuck:{sid}"]["text"] == "alice has been provisioning for 2 hours"
    finish(client, token, next_job(client, token)["id"])
    assert server(alice)["state"] == "running" and f"stuck:{sid}" not in collect()

    beat(client, token, [{"label": "alice", "running": False}])
    assert collect()[f"down:{sid}"]["text"] == "alice is down" and collect()[f"down:{sid}"]["settle"] == alerts.SETTLE
    beat(client, token, [{"label": "alice", "running": True, "health": "unhealthy"}])
    assert collect()[f"down:{sid}"]["text"] == "alice is unhealthy"
    beat(client, token, [{"label": "alice", "running": True, "health": "healthy"}])
    assert f"down:{sid}" not in collect()
    with closing(db.connect()) as conn:                        # a stopped server's container is meant to be down
        conn.execute("UPDATE hosted_servers SET state = 'stopped' WHERE id = ?", (sid,))
        conn.commit()
    beat(client, token, [{"label": "alice", "running": False}])
    assert f"down:{sid}" not in collect()


def test_a_server_waiting_a_host_silent_or_full_and_a_missing_record(client, hosting):
    host_id, token = make_host()
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosts SET accepting = 0")
        conn.commit()
    bob = make_account("bob", "plus")
    found = collect()[f"waiting:{server(bob)['id']}"]
    assert (found["text"], found["settle"]) == ("bob is waiting for a host with room", alerts.SETTLE)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosts SET accepting = 1")
        conn.commit()
    beat(client, token, [])                                    # places bob
    sid = server(bob)["id"]
    assert server(bob)["host_id"] == host_id and f"waiting:{sid}" not in collect()

    # bob's 768 MB of the 800 the host can place (its memory less the 1024 MB reserve); its disk 90 % full
    beat(client, token, [], memory_mb=1824, disk_mb=1000, disk_used_mb=900)
    found = collect()
    assert found[f"host_full:{host_id}:memory"]["text"] == ("vps-1 has 768 of the 800 MB it can place committed "
                                                             "to servers")
    assert found[f"host_full:{host_id}:disk"]["text"] == "vps-1's disk is 90% full"
    beat(client, token, [])
    assert not [k for k in collect() if k.startswith("host_full")]

    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosts SET last_seen_at = ?", (db.after(-20 * 60),))
        conn.commit()
    assert collect()[f"host_stale:{host_id}"]["text"].startswith("vps-1 has not reported since ")
    beat(client, token, [])
    assert f"host_stale:{host_id}" not in collect()

    with closing(db.connect()) as conn:                        # a host with an address of its own: a record per server
        conn.execute("UPDATE hosts SET public_ip = '203.0.113.7'")
        conn.execute("UPDATE hosted_servers SET report = ? WHERE id = ?",
                     (json.dumps({"dns": {"error": "zone not found", "at": db.now()}}), sid))
        conn.commit()
    found = collect()[f"dns:{sid}"]
    assert (found["text"], found["settle"]) == ("bob has no DNS record yet: zone not found", alerts.SETTLE)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosted_servers SET dns_target = '203.0.113.7'")
        conn.commit()
    assert f"dns:{sid}" not in collect()


def test_an_unmatched_webhook_and_open_sign_up(client):
    with closing(db.connect()) as conn:
        for event, outcome, days in (("evt_new", "mismatch", 0), ("evt_lost", "unknown", 1), ("evt_old", "unknown", 8),
                                     ("evt_fine", "applied", 0)):
            conn.execute("INSERT INTO billing_events (id, type, outcome, received_at) VALUES (?, ?, ?, ?)",
                         (event, "checkout.session.completed", outcome, db.after(-days * 86400)))
        conn.commit()
    found = collect()
    assert sorted(k for k in found if k.startswith("billing:")) == ["billing:evt_lost", "billing:evt_new"]
    new = found["billing:evt_new"]
    assert new["text"] == "Stripe event checkout.session.completed could not be matched to an account"
    assert new["link"] == "#billing"
    assert "signup:unguarded" not in found
    set_setting("registration", "open")
    assert collect()["signup:unguarded"]["link"] == "#settings"
    set_setting("turnstile_sitekey", "0xSITE")
    set_setting("turnstile_secret", "secret")
    assert "signup:unguarded" not in collect()


# --- the table and the mail ------------------------------------------------------

def test_a_problem_is_mailed_once_and_only_once_it_has_lasted(client, hosting):
    operator(client)
    set_setting("registration", "open")                        # settles at once
    key = f"waiting:{server(make_account('bob', 'plus'))['id']}"   # no host: waits, which settles in 10 minutes
    assert [a["key"] for a in sync()] == ["signup:unguarded"]
    assert row("signup:unguarded")["mailed_at"] and row(key)["mailed_at"] is None
    assert alert_mails() == []                                 # sync marks; the caller sends
    assert sync() == []                                        # mailed once per opening
    settle(key, 11)
    assert [a["key"] for a in sync()] == [key]
    assert row(key)["last_at"] >= row(key)["first_at"]


def test_one_mail_lists_them_to_every_admin_or_the_address(client):
    operator(client)
    other_admin("second")
    other_admin("unconfirmed", verified=False)
    register(client, "plainuser")
    two = [{"key": "a", "kind": "x", "text": "one thing", "link": "#servers"},
           {"key": "b", "kind": "y", "text": "another", "link": "#billing"}]
    alerts.notify(two)
    sent = alert_mails()
    assert sorted(m["to"] for m in sent) == ["operator@example.org", "second@example.org"]
    assert {m["subject"] for m in sent} == {"Gamma Cloud: 2 things need attention"}
    assert "one thing: http://testserver/admin#servers" in sent[0]["body"]
    assert "another: http://testserver/admin#billing" in sent[0]["body"]
    mail.outbox.clear()
    set_setting("alert_email", "ops@example.org")
    alerts.notify(two[:1])
    assert [(m["to"], m["subject"]) for m in alert_mails()] == [("ops@example.org", "Gamma Cloud: one thing")]
    mail.outbox.clear()
    alerts.notify([])
    set_setting("alerts", "off")
    alerts.notify(two)
    assert alert_mails() == []


def test_with_alerts_off_nothing_is_marked_so_turning_them_on_mails_what_is_open(client):
    operator(client)
    set_setting("alerts", "off")
    set_setting("registration", "open")
    assert sync() == [] and row("signup:unguarded")["mailed_at"] is None
    set_setting("alerts", "on")
    assert [a["key"] for a in sync()] == ["signup:unguarded"]


def test_a_problem_that_comes_back_opens_afresh(client):
    operator(client)
    set_setting("registration", "open")
    sync()
    with closing(db.connect()) as conn:
        alerts.dismiss(conn, "signup:unguarded", "test")
        conn.commit()
    settle("signup:unguarded", 60)
    set_setting("registration", "invite")
    assert sync() == []
    gone = row("signup:unguarded")
    assert gone["resolved_at"] and gone["dismissed_at"] and gone["mailed_at"]
    set_setting("registration", "open")
    assert [a["key"] for a in sync()] == ["signup:unguarded"]   # mailed again: a new opening
    back = row("signup:unguarded")
    assert back["resolved_at"] is None and back["dismissed_at"] is None and back["first_at"] > gone["first_at"]


def test_a_dismissed_alert_is_not_mailed(client, hosting):
    operator(client)
    make_account("bob", "plus")
    sync()
    key = next(k for k in collect() if k.startswith("waiting:"))
    with closing(db.connect()) as conn:
        alerts.dismiss(conn, key, "test")
        conn.commit()
    settle(key, 11)
    assert sync() == [] and row(key)["mailed_at"] is None


# --- where it runs ---------------------------------------------------------------

def test_a_job_result_and_a_heartbeat_mail_what_is_due(client, hosting):
    operator(client)
    _, token = make_host()
    alice = make_account("alice", "plus")
    sid = server(alice)["id"]
    job = next_job(client, token)
    finish(client, token, job["id"], "failed", {"error": "pull failed"})
    assert [m["subject"] for m in alert_mails()] == ["Gamma Cloud: create job for alice failed: pull failed"]
    assert alert_mails()[0]["to"] == "operator@example.org"
    with closing(db.connect()) as conn:
        fleet.retry(conn, job["id"], "test")
        conn.commit()
    finish(client, token, next_job(client, token)["id"])
    assert row(f"job:{job['id']}")["resolved_at"]
    mail.outbox.clear()
    beat(client, token, [{"label": "alice", "running": False}])
    assert row(f"down:{sid}") and alert_mails() == []         # one bad heartbeat is not a mail
    settle(f"down:{sid}", 11)
    beat(client, token, [{"label": "alice", "running": False}])
    assert [m["subject"] for m in alert_mails()] == ["Gamma Cloud: alice is down"]
    beat(client, token, [{"label": "alice", "running": False}])
    assert len(alert_mails()) == 1


def test_a_failure_of_the_alerts_never_loses_the_heartbeat(client, hosting, monkeypatch):
    _, token = make_host()

    def broken(conn):
        raise RuntimeError("bad row")
    monkeypatch.setattr(alerts, "collect", broken)
    beat(client, token, [], memory_used_mb=4321)
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT memory_used_mb FROM hosts").fetchone()[0] == 4321


def test_the_hourly_pass_returns_what_is_due_and_purges_the_old(client, hosting):
    operator(client)
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO alerts (key, kind, text, first_at, last_at, resolved_at) VALUES "
                     "('old', 'x', 'x', ?, ?, ?), ('recent', 'x', 'x', ?, ?, ?)",
                     (db.after(-40 * 86400),) * 3 + (db.after(-86400),) * 3)
        conn.commit()
    set_setting("registration", "open")
    with closing(db.connect()) as conn:
        due = hosted.tick(conn)
        conn.commit()
    assert [a["key"] for a in due] == ["signup:unguarded"]
    assert row("old") is None and row("recent")
    set_setting("registration", "invite")
    assert sync() == []                                        # resolved, so the next opening is mailed again
    set_setting("alert_email", "ops@example.org")
    set_setting("registration", "open")
    from gammacloud import app
    mail.outbox.clear()
    app.purge()                                                # the hourly pass mails after its commit
    assert [m["to"] for m in alert_mails()] == ["ops@example.org"]


# --- the Admin page ----------------------------------------------------------------

def test_the_alert_endpoints_and_dismissal(client):
    register(client, "operator")
    assert client.get("/api/admin/alerts").status_code == 403
    make_admin("operator")
    set_setting("registration", "open")
    [a] = client.get("/api/admin/alerts").json()["alerts"]     # brought up to date by the read
    assert a["key"] == "signup:unguarded" and a["dismissed_at"] is None
    r = client.post("/api/admin/alerts/signup:unguarded/dismiss")
    assert r.status_code == 200 and r.json()["alert"]["dismissed_at"]
    assert client.get("/api/admin/overview").json()["alerts"] == []
    assert client.get("/api/admin/alerts").json()["alerts"][0]["dismissed_at"]
    events = client.get("/api/admin/audit").json()["audit"]
    assert any(e["event"] == "alert.dismiss" and e["detail"] == "signup:unguarded" for e in events)
    assert client.post("/api/admin/alerts/nope/dismiss").status_code == 404
    set_setting("registration", "invite")
    assert client.get("/api/admin/alerts").json()["alerts"] == []
    [resolved] = client.get("/api/admin/alerts", params={"all": 1}).json()["alerts"]
    assert resolved["key"] == "signup:unguarded" and resolved["resolved_at"]
    assert client.post("/api/admin/alerts/signup:unguarded/dismiss").status_code == 404


def test_the_overview(client, hosting):
    operator(client)
    register(client, "newbie")                                 # unconfirmed
    gone = make_account("gone", "free")
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET deleted_at = ? WHERE id = ?", (db.now(), gone))
        conn.commit()
    client.post("/api/login", json={"login": "operator", "password": "correct horse battery"})
    make_host()
    make_account("alice", "plus")
    d = client.get("/api/admin/overview").json()
    assert d["accounts"] == {"total": 3, "new_7d": 3, "unverified": 1, "deleted": 1}
    assert d["fleet"] == {"hosts": {"fresh": 1, "stale": 0},
                          "servers": {"by_state": {"provisioning": 1}, "outdated": 0},
                          "jobs": {"queued": 1, "running": 0, "failed": 0}}
    assert d["billing"]["enabled"] is False and d["billing"]["paying"] == 0 and "mrr_usd" in d["billing"]
    assert d["settings"] == {"registration": "invite", "turnstile_on": False, "plans_on_sale": ["lite", "plus", "pro"],
                             "alerts": True, "alert_email": "", "fleet_auto_upgrade": False,
                             "fleet_image_tag": fleet.default_tag()}
    assert d["alerts"] == []
    page = client.get("/admin").text
    assert "<button class=on data-tab=overview>" in page and "id=tab-overview" in page and "loadOverview" in page
    assert "id=setalerts" in page and "Send test alert" in page and "<div id=tab-accounts hidden>" in page


def test_the_test_alert(client):
    register(client, "operator")
    make_admin("operator")
    assert client.post("/api/admin/alerts/test").status_code == 409   # no confirmed address to send to
    verify(client)
    r = client.post("/api/admin/alerts/test")
    assert r.status_code == 200 and r.json()["to"] == ["operator@example.org"]
    [sent] = alert_mails()
    assert sent["subject"] == "Gamma Cloud: Test alert from the Admin page" and "/admin#settings" in sent["body"]
    set_setting("alerts", "off")                               # the test goes out all the same
    set_setting("alert_email", "ops@example.org")
    assert client.post("/api/admin/alerts/test").json()["to"] == ["ops@example.org"]
    for _ in range(3):
        client.post("/api/admin/alerts/test")
    assert client.post("/api/admin/alerts/test").status_code == 429

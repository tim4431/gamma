"""The fleet's history (docs/dev/hosted.md "History"): samples merged by
the hour with errors summed, series, the purge, what the heartbeat (every
container too) and the sync record, the trends on the Servers tab's rows,
a host's latest CPU figure on the Machines tab and the metrics endpoint."""

import json
from contextlib import closing

from conftest import make_admin, register
from test_fleet import bearer
from test_hosted import REPORT, basic, credentials, hosting, make_account, make_host, server  # noqa: F401

from gammacloud import db, metrics


def rows(kind=None):
    with closing(db.connect()) as conn:
        sql, args = "SELECT * FROM metrics", ()
        if kind:
            sql, args = sql + " WHERE kind = ?", (kind,)
        return [{**dict(r), "data": json.loads(r["data"])} for r in conn.execute(sql + " ORDER BY at", args).fetchall()]


def record(kind, ref, data):
    with closing(db.connect()) as conn:
        metrics.record(conn, kind, ref, data)
        conn.commit()


def put(kind, ref, hours_ago, data):
    """A sample of an hour that has passed."""
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO metrics (kind, ref, at, data) VALUES (?, ?, ?, ?)",
                     (kind, ref, metrics.hour(db.after(-hours_ago * 3600)), json.dumps(data)))
        conn.commit()


def test_samples_merge_by_the_hour_and_errors_add_up():
    record("server", "s1", {"memory_mb": 100, "errors": 2, "cpu_pct": None, "bad": "x"})
    record("server", "s1", {"memory_mb": 150, "errors": 3, "cpu_pct": 4.5})
    record("server", "s2", {"memory_mb": 1})
    [one, _] = rows()
    assert one["at"] == metrics.hour() and one["at"].endswith(":00:00.000Z")
    assert one["data"] == {"memory_mb": 150, "errors": 5, "cpu_pct": 4.5}
    record("server", "s1", {})                                 # nothing known: nothing written
    assert len(rows()) == 2


def test_series_purge_and_trends():
    put("server", "s1", 50, {"memory_mb": 10})
    put("server", "s1", 30, {"memory_mb": 20, "accounts": 1})
    put("server", "s1", 24 * 31, {"memory_mb": 5})
    record("server", "s1", {"memory_mb": 30, "cpu_pct": 1.0})
    with closing(db.connect()) as conn:
        assert [p["memory_mb"] for p in metrics.series(conn, "server", "s1", 48)] == [20, 30]
        assert [p["memory_mb"] for p in metrics.series(conn, "server", "s1", 720)] == [10, 20, 30]
        assert metrics.series(conn, "server", "s1", 1) == [{"at": metrics.hour(), "memory_mb": 30, "cpu_pct": 1.0}]
        assert metrics.trends(conn, "server", ("memory_mb",)) == {"s1": [
            {"at": metrics.hour(db.after(-30 * 3600)), "memory_mb": 20}, {"at": metrics.hour(), "memory_mb": 30}]}
        metrics.purge(conn)
        conn.commit()
    assert [r["data"]["memory_mb"] for r in rows()] == [10, 20, 30]


def test_the_heartbeat_and_the_sync_feed_the_history(client, hosting):
    host_id, token = make_host()
    alice = make_account("alice", "plus")
    sid = server(alice)["id"]
    body = {"agent_version": "1", "memory_mb": 8192, "disk_mb": 500_000, "memory_used_mb": 2000, "disk_used_mb": 30_000,
            "cpu_pct": 7.66, "containers": [{"label": "alice", "running": True, "health": "healthy", "memory_mb": 300,
                                             "data_mb": 42, "cpu_pct": 12.34, "restarts": 1},
                                            {"label": "stranger", "running": True}]}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    [host] = rows("host")
    assert host["ref"] == host_id
    assert host["data"] == {"memory_used_mb": 2000, "disk_used_mb": 30_000, "cpu_pct": 7.7, "committed_mb": 768,
                            "servers": 1}
    auth = basic(*credentials(alice))
    for extra in ({"errors": 2, "active_accounts": 1}, {"errors": 3, "accounts": 2}):
        assert client.post("/api/hosted/sync", json={**REPORT, **extra}, headers=auth).status_code == 200
    [s] = rows("server")
    assert s["ref"] == sid
    assert s["data"] == {"memory_mb": 300, "cpu_pct": 12.3, "data_mb": 42, "restarts": 1, "uploads_bytes": 5 << 20,
                         "data_bytes": 9 << 20, "accounts": 2, "active_accounts": 0, "errors": 5}


def test_the_heartbeat_samples_every_container(client, hosting):
    from test_fleet import docker
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    body = {"memory_mb": 8192, "disk_mb": 500_000, "docker": [
        docker("share", memory_mb=300, cpu_pct=1.25), docker("caddy", status="exited", memory_mb=0, cpu_pct=None)]}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    assert {r["ref"]: r["data"] for r in rows("container")} == {f"{host_id}:share": {"memory_mb": 300, "cpu_pct": 1.2},
                                                                 f"{host_id}:caddy": {"memory_mb": 0}}
    r = client.get("/api/admin/metrics", params={"kind": "container", "ref": f"{host_id}:share"}).json()
    assert [p["memory_mb"] for p in r["points"]] == [300]
    put("container", f"{host_id}:share", 24 * 31, {"memory_mb": 5})
    with closing(db.connect()) as conn:
        metrics.purge(conn)
        conn.commit()
    assert len(rows("container")) == 2


def test_a_hosts_cpu_use_is_its_latest_samples(client, hosting):
    """The Machines tab's CPU figure: ``cpu_pct`` of the host's newest
    sample, which keeps the hour's last value the agent knew."""
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    other, _ = make_host("vps-2")

    def beat(**over):
        body = {"memory_mb": 8192, "disk_mb": 500_000, **over}
        assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200

    def cpu():
        return {m["name"]: m["cpu_pct"] for m in client.get("/api/admin/machines").json()["machines"]}
    put("host", host_id, 5, {"cpu_pct": 80.0})
    beat()                                    # an agent before 0.3.1, or a first heartbeat: none this hour
    assert cpu() == {"vps-1": None, "vps-2": None}
    beat(cpu_pct=12.34)
    assert cpu()["vps-1"] == 12.3
    for unknown in (None, "busy", -3):
        beat(cpu_pct=unknown)
        assert cpu()["vps-1"] == 12.3
    with closing(db.connect()) as conn:
        latest = metrics.latest(conn, "host")
    assert latest[host_id] == {"at": metrics.hour(), "memory_used_mb": 0, "disk_used_mb": 0, "cpu_pct": 12.3,
                               "committed_mb": 0, "servers": 0}
    assert set(latest) == {host_id, other} and "cpu_pct" not in latest[other]


def test_the_trends_and_the_metrics_endpoint(client, hosting):
    register(client, "operator")
    host_id, _ = make_host()
    sid = server(make_account("alice", "plus"))["id"]
    record("server", sid, {"memory_mb": 300, "cpu_pct": 2.0, "data_mb": 40, "errors": 1, "accounts": 1})
    put("server", sid, 5, {"memory_mb": 200})
    put("server", sid, 100, {"memory_mb": 100})
    record("host", host_id, {"memory_used_mb": 900, "disk_used_mb": 50, "committed_mb": 768, "servers": 1})
    assert client.get("/api/admin/metrics", params={"kind": "server", "ref": sid}).status_code == 403
    make_admin("operator")
    [s] = client.get("/api/admin/servers").json()["servers"]
    assert [sorted(p) for p in s["trend"]] == [["at", "memory_mb"], ["at", "cpu_pct", "data_mb", "memory_mb"]]
    [h] = client.get("/api/admin/hosts").json()["hosts"]
    assert "trend" not in h   # a host's history is on the metrics endpoint only
    r = client.get("/api/admin/metrics", params={"kind": "host", "ref": host_id}).json()
    assert r["points"] == [{"at": metrics.hour(), "memory_used_mb": 900, "disk_used_mb": 50, "committed_mb": 768, "servers": 1}]
    r = client.get("/api/admin/metrics", params={"kind": "server", "ref": sid, "hours": 24}).json()
    assert r["hours"] == 24 and [p["memory_mb"] for p in r["points"]] == [200, 300] and r["points"][1]["errors"] == 1
    r = client.get("/api/admin/metrics", params={"kind": "server", "ref": sid, "hours": 5000}).json()
    assert r["hours"] == 720 and [p["memory_mb"] for p in r["points"]] == [100, 200, 300]
    assert client.get("/api/admin/metrics", params={"kind": "account", "ref": sid}).status_code == 400
    assert client.get("/api/admin/metrics", params={"kind": "host", "ref": "h_none"}).json()["points"] == []

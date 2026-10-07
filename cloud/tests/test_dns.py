"""DNS records for the servers on a routed host (a host with a public IP of
its own): the Cloudflare client, reconcile against a fake zone, placement
while DNS is off, the kick after a commit, and the admin's fields, pills
and CLI (docs/dev/hosted.md "Deployment")."""

import io
import json
import urllib.error
from contextlib import closing

import pytest
from conftest import make_admin, register
from test_hosted import DOMAIN, hosting, make_account, make_host, server, tick  # noqa: F401

import manage
from gammacloud import config, db, dns, fleet, hosted

REAL_KICK = dns.kick
IP = "198.51.100.7"


class FakeCloudflare:
    """A zone in memory, with Cloudflare's 404 for a record that is not
    there. ``fail`` makes every call raise; ``kicks`` records ``dns.kick``."""

    def __init__(self):
        self.records, self.calls, self.kicks, self.fail, self.n = {}, [], [], "", 0

    def _call(self, *call):
        self.calls.append(call)
        if self.fail:
            raise dns.DNSError(self.fail, 403)

    def _missing(self, record_id):
        if record_id not in self.records:
            raise dns.DNSError("Cloudflare answered 404: 81044 Record does not exist.", 404)

    def find(self, name):
        self._call("find", name)
        return [dict(r) for r in self.records.values() if r["name"] == name]

    def create(self, record):
        self._call("create", record["name"])
        self.n += 1
        self.records[f"r{self.n}"] = {**record, "id": f"r{self.n}"}
        return dict(self.records[f"r{self.n}"])

    def update(self, record_id, record):
        self._call("update", record_id)
        self._missing(record_id)
        self.records[record_id] = {**record, "id": record_id}
        return dict(self.records[record_id])

    def delete(self, record_id):
        self._call("delete", record_id)
        self._missing(record_id)
        del self.records[record_id]


@pytest.fixture
def cf(monkeypatch, hosting):
    """DNS on, against a fake zone; a kick is only recorded, and the tests
    reconcile themselves."""
    fake = FakeCloudflare()
    monkeypatch.setattr(config, "CF_API_TOKEN", "cf-token")
    monkeypatch.setattr(config, "CF_ZONE_ID", "zone-1")
    monkeypatch.setattr(dns, "kick", lambda gone="": fake.kicks.append(gone))
    dns.set_client(fake)
    yield fake
    dns.set_client(None)


def routed_host(name="edge-1", ip=IP, memory_mb=8192):
    with closing(db.connect()) as conn:
        host, token = fleet.add_host(conn, name, public_ip=ip, actor="test")
        fleet.heartbeat(conn, dict(host), {"agent_version": "1", "memory_mb": memory_mb, "disk_mb": 500_000,
                                           "memory_used_mb": 1024, "disk_used_mb": 10_000})
        conn.commit()
    return host["id"], token


def reconcile(gone=()):
    with closing(db.connect()) as conn:
        return dns.reconcile(conn, gone)


def counts(created=0, updated=0, deleted=0, failed=0):
    return {"created": created, "updated": updated, "deleted": deleted, "failed": failed}


def view(account_id):
    with closing(db.connect()) as conn:
        return hosted.admin_view(conn, conn.execute("SELECT * FROM hosted_servers WHERE account_id = ?",
                                                    (account_id,)).fetchone())


def set_ip(host_id, ip):
    with closing(db.connect()) as conn:
        fleet.update_host(conn, host_id, public_ip=ip, actor="test")
        conn.commit()


def admin(client):
    register(client, "operator")
    make_admin("operator")


# --- the client -----------------------------------------------------------------

class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_the_client_speaks_cloudflares_api(monkeypatch):
    seen, answers = [], []

    def urlopen(req, timeout):
        seen.append(req)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Answer(json.dumps(answer).encode())

    monkeypatch.setattr(dns.urllib.request, "urlopen", urlopen)
    c = dns.CloudflareClient("tok", "zone-1")
    answers += [{"success": True, "result": [{"id": "r1", "type": "A"}]}, {"success": True, "result": {"id": "r2"}}]
    assert c.find("alice-user.gammapdf.com") == [{"id": "r1", "type": "A"}]
    assert c.create(dns.record_for("s_1", "alice", "2001:DB8::1"))["id"] == "r2"
    base = "https://api.cloudflare.com/client/v4/zones/zone-1/dns_records"
    assert seen[0].get_method() == "GET" and seen[0].full_url == base + "?name=alice-user.gammapdf.com&per_page=100"
    assert seen[1].get_method() == "POST" and seen[1].full_url == base
    assert seen[1].get_header("Authorization") == "Bearer tok"
    body = json.loads(seen[1].data)
    assert body["type"] == "AAAA" and body["content"] == "2001:DB8::1" and body["proxied"] is True
    assert body["ttl"] == 1 and "s_1" in body["comment"]

    gone = {"success": False, "errors": [{"code": 81044, "message": "Record does not exist."}]}
    answers += [urllib.error.HTTPError(base + "/r9", 404, "Not Found", {}, io.BytesIO(json.dumps(gone).encode())),
                {"success": False, "errors": [{"code": 9005, "message": "Content is invalid"}]},
                OSError("connection refused")]
    with pytest.raises(dns.DNSError) as e:
        c.delete("r9")
    assert seen[2].get_method() == "DELETE" and seen[2].full_url == base + "/r9"
    assert e.value.status == 404 and "81044 Record does not exist." in str(e.value)
    with pytest.raises(dns.DNSError) as e:
        c.update("r1", {"type": "A"})
    assert seen[3].get_method() == "PATCH" and e.value.status == 0 and "9005 Content is invalid" in str(e.value)
    with pytest.raises(dns.DNSError) as e:
        c.find("x")
    assert "could not be reached" in str(e.value)


# --- reconcile --------------------------------------------------------------------

def test_a_server_on_a_routed_host_gets_its_record_once(client, cf):
    make_host("vps-1", memory_mb=2048)                     # behind the entrance: less room, so not chosen
    host_id, _ = routed_host()
    alice = make_account("alice", "plus")
    assert server(alice)["host_id"] == host_id and cf.kicks == [""]     # the placement kicked a run
    assert view(alice)["dns"] == "pending" and view(alice)["host_ip"] == IP
    assert reconcile() == counts(created=1)
    [record] = cf.records.values()
    assert record == {"id": "r1", "type": "A", "name": f"alice-user.{DOMAIN}", "content": IP, "proxied": True,
                      "ttl": 1, "comment": f"Gamma hosted server {server(alice)['id']}"}
    row = server(alice)
    assert (row["dns_record_id"], row["dns_target"]) == ("r1", IP) and view(alice)["dns"] == "ok"
    calls = len(cf.calls)
    assert reconcile() == counts() and len(cf.calls) == calls         # nothing to change: nothing called
    tick()                                                            # the hourly pass reconciles too
    assert len(cf.calls) == calls

    bob = make_account("bob", "free")
    assert server(bob) is None and view(alice)["dns"] == "ok"
    with closing(db.connect()) as conn:                               # a server behind the entrance needs none
        conn.execute("UPDATE accounts SET plan = 'plus', granted_plan = 'plus' WHERE id = ?", (bob,))
        conn.execute("UPDATE hosts SET accepting = 0 WHERE id = ?", (host_id,))
        hosted.plan_changed(conn, bob)
        conn.commit()
    assert server(bob)["host_id"] != host_id and view(bob)["dns"] == "" and view(bob)["host_ip"] == ""
    assert reconcile() == counts() and len(cf.records) == 1


def test_a_new_address_moves_the_record_and_none_removes_it(client, cf):
    host_id, _ = routed_host()
    alice = make_account("alice", "plus")
    reconcile()
    cf.kicks.clear()
    set_ip(host_id, " 2001:DB8::7 ")
    assert cf.kicks == [""] and view(alice)["dns"] == "pending"      # the change kicked a run
    assert reconcile() == counts(updated=1)
    assert cf.records["r1"]["type"] == "AAAA" and cf.records["r1"]["content"] == "2001:db8::7"
    assert server(alice)["dns_target"] == "2001:db8::7" and view(alice)["dns"] == "ok"
    set_ip(host_id, "")
    assert view(alice)["dns"] == ""
    assert reconcile() == counts(deleted=1) and cf.records == {}
    row = server(alice)
    assert (row["dns_record_id"], row["dns_target"]) == ("", "")
    assert reconcile() == counts()


def test_a_deleted_server_or_one_behind_the_entrance_has_no_record(client, cf):
    host_id, _ = routed_host()
    plain, _ = make_host("vps-1", memory_mb=2048)
    alice, bob = make_account("alice", "plus"), make_account("bob", "plus")
    assert reconcile() == counts(created=2)
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, server(alice)["id"], "delete", "test")
        conn.execute("UPDATE hosted_servers SET host_id = ? WHERE account_id = ?", (plain, bob))   # as if moved
        conn.commit()
    assert "" in cf.kicks and view(alice)["dns"] == "" and view(bob)["dns"] == ""
    assert reconcile() == counts(deleted=2) and cf.records == {}
    assert reconcile() == counts()


def test_a_failure_is_kept_on_the_server_and_tried_again(client, cf):
    routed_host()
    alice = make_account("alice", "plus")
    cf.fail = "Cloudflare answered 403: 10000 Authentication error"
    assert reconcile() == counts(failed=1)
    row = server(alice)
    error = json.loads(row["report"])["dns"]
    assert row["dns_record_id"] == "" and "Authentication error" in error["error"] and error["at"]
    v = view(alice)
    assert v["dns"] == "pending" and "Authentication error" in v["report"]["dns"]["error"]
    assert reconcile() == counts(failed=1)                            # still failing: kept, tried again
    cf.fail = ""
    assert reconcile() == counts(created=1)
    assert "dns" not in json.loads(server(alice)["report"]) and view(alice)["dns"] == "ok"


def test_a_record_removed_by_hand_is_made_again_and_one_by_that_name_taken_over(client, cf):
    host_id, _ = routed_host()
    name = f"alice-user.{DOMAIN}"
    cf.records["old"] = {"id": "old", "type": "A", "name": name, "content": "192.0.2.1", "proxied": False}
    alice = make_account("alice", "plus")
    assert reconcile() == counts(updated=1)                          # the record by that name, not a second one
    assert list(cf.records) == ["old"] and cf.records["old"]["content"] == IP and cf.records["old"]["proxied"] is True
    del cf.records["old"]                                             # removed in Cloudflare's dashboard
    set_ip(host_id, "198.51.100.8")
    assert reconcile() == counts(created=1)
    assert server(alice)["dns_record_id"] == "r1" and cf.records["r1"]["content"] == "198.51.100.8"
    del cf.records["r1"]
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, server(alice)["id"], "delete", "test")
        conn.commit()
    assert reconcile() == counts(deleted=1) and server(alice)["dns_record_id"] == ""   # gone already: done


def test_a_purged_accounts_record_goes_by_its_id(client, cf):
    routed_host()
    alice = make_account("alice", "plus")
    reconcile()
    record_id = server(alice)["dns_record_id"]
    with closing(db.connect()) as conn:
        hosted.purge_account(conn, alice, "test")
        conn.commit()
    assert server(alice) is None and record_id in cf.kicks
    assert reconcile((record_id, "r-unknown")) == counts(deleted=1) and cf.records == {}


def test_nothing_is_called_and_routed_hosts_take_no_servers_while_dns_is_off(client, hosting, monkeypatch):
    fake = FakeCloudflare()
    dns.set_client(fake)
    try:
        host_id, _ = routed_host()
        alice = make_account("alice", "plus")
        assert server(alice)["host_id"] == "" and json.loads(server(alice)["report"])["note"] == hosted.WAITING
        assert dns.kick() is None and reconcile() == counts() and fake.calls == []
        with closing(db.connect()) as conn:
            assert fleet.hosts(conn)[0]["dns"] == "off"
        plain, _ = make_host("vps-1")                                  # its first heartbeat places the server
        with closing(db.connect()) as conn:
            hosted.place_waiting(conn)
            conn.commit()
        assert server(alice)["host_id"] == plain
        monkeypatch.setattr(config, "CF_API_TOKEN", "cf-token")
        monkeypatch.setattr(config, "CF_ZONE_ID", "zone-1")
        monkeypatch.setattr(dns, "kick", lambda gone="": None)
        with closing(db.connect()) as conn:
            row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
            assert fleet.public_host(row)["dns"] == "on"
            assert fleet.place(conn, 768, 6144)["id"] == host_id       # the most free memory, and routed hosts count
        monkeypatch.setattr(config, "HOSTED_DOMAIN", "")
        assert not dns.enabled()                                      # no domain, no names to make
    finally:
        dns.set_client(None)


def test_a_kick_runs_once_the_transaction_that_kicked_it_commits(client, cf):
    host_id, _ = routed_host()
    alice = make_account("alice", "plus")
    assert cf.kicks and cf.calls == []                                # recorded, not run
    with closing(db.connect()) as conn:
        db.begin_write(conn)
        conn.execute("UPDATE hosts SET public_ip = '198.51.100.8' WHERE id = ?", (host_id,))
        thread = REAL_KICK()
        thread.join(0.5)
        assert thread.is_alive() and cf.calls == []                   # waiting for the lock this transaction holds
        conn.commit()
    thread.join(10)
    assert not thread.is_alive()
    [record] = cf.records.values()
    assert record["content"] == "198.51.100.8" and server(alice)["dns_target"] == "198.51.100.8"


# --- the admin's side ---------------------------------------------------------------

def test_the_admin_sets_a_hosts_public_ip_and_sees_the_pills(client, cf, monkeypatch):
    admin(client)
    r = client.post("/api/admin/hosts", json={"name": "edge-1", "public_ip": " 2001:DB8::1 "})
    assert r.status_code == 200
    host, token = r.json()["host"], r.json()["token"]
    assert host["public_ip"] == "2001:db8::1" and host["dns"] == "on" and host["address"] == ""
    assert r.json()["bootstrap"] == (f"curl -fsSL {fleet.BOOTSTRAP_URL} | bash -s -- --token {token} "
                                     f"--account-url http://testserver --edge --domain {DOMAIN}")
    r = client.post("/api/admin/hosts", json={"name": "vps-1"})
    assert r.json()["host"]["dns"] == "" and "--edge" not in r.json()["bootstrap"]
    for bad in ("not-an-ip", "300.1.2.3", "2001:db8::1/64", "edge.example.org"):
        assert client.post("/api/admin/hosts", json={"name": "edge-2", "public_ip": bad}).status_code == 400
        assert client.patch(f"/api/admin/hosts/{host['id']}", json={"public_ip": bad}).status_code == 400
    r = client.patch(f"/api/admin/hosts/{host['id']}", json={"public_ip": "198.51.100.9"})
    assert r.status_code == 200 and r.json()["host"]["public_ip"] == "198.51.100.9"
    r = client.patch(f"/api/admin/hosts/{host['id']}", json={"name": "edge-a"})
    assert r.json()["host"]["public_ip"] == "198.51.100.9"            # left alone when not named
    monkeypatch.setattr(config, "CF_API_TOKEN", "")
    hosts = {h["name"]: h for h in client.get("/api/admin/hosts").json()["hosts"]}
    assert hosts["edge-a"]["dns"] == "off" and hosts["vps-1"]["dns"] == ""
    items = {(g["name"], i["name"]): i for g in client.get("/api/admin/config").json()["groups"] for i in g["items"]}
    assert items["Hosting", "DNS records"]["state"] == "off" and "token" in items["Hosting", "DNS records"]["note"]

    monkeypatch.setattr(config, "CF_API_TOKEN", "cf-token")
    client.post("/api/fleet/heartbeat", json={"memory_mb": 8000, "disk_mb": 200000},
                headers={"Authorization": f"Bearer {token}"})
    alice = make_account("alice", "plus")
    [s] = [x for x in client.get("/api/admin/servers").json()["servers"] if x["account_id"] == alice]
    assert s["host"] == "edge-a" and s["host_ip"] == "198.51.100.9" and s["dns"] == "pending"
    reconcile()
    [s] = [x for x in client.get("/api/admin/servers").json()["servers"] if x["account_id"] == alice]
    assert s["dns"] == "ok" and s["dns_target"] == "198.51.100.9"
    page = client.get("/admin").text
    assert "name=public_ip" in page and "Public IP…" in page and "no dns token: closed" in page and "dnsPill" in page


def test_the_cli_adds_a_routed_host(capsys, client, cf):
    manage.main(["add-host", "edge-1", "--public-ip", IP])
    out = capsys.readouterr().out
    assert "GAMMA_FLEET_HOST_TOKEN=gf_" in out and "bootstrap.sh | bash -s -- --token gf_" in out and "--edge" in out
    manage.main(["add-host", "vps-1"])
    assert "--edge" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        manage.main(["add-host", "edge-2", "--public-ip", "nope"])
    manage.main(["hosts"])
    out = capsys.readouterr().out
    assert f"ip={IP} dns=on" in out and out.count("ip=") == 1

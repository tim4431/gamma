"""DNS records for the hosted servers on a host with a proxy of its own
(docs/dev/hosted.md "Deployment").

A host with a ``public_ip`` is a *routed* host: it runs its own Caddy
(``cloud/fleet/deploy/``, the ``edge`` profile), and every server placed on
it gets a proxied Cloudflare record ``<label><HOSTED_SUFFIX>.<HOSTED_DOMAIN>``
(``A`` or ``AAAA`` by the address's family) pointing at that address, with a
comment naming the server. A host without one sits behind the entrance's
Caddy and the zone's wildcard record, and its servers need none. A named
record wins over the wildcard at Cloudflare.

``reconcile`` makes the zone match the rows: the record a server should
have (its host's address, while it is not deleted and its host is routed)
against the one it has (``dns_record_id``, ``dns_target``). It reads, ends
the transaction, calls Cloudflare, and writes each answer in a short
transaction of its own, so no call is made while cloud.db's write lock is
held. A failed call is logged and kept in the server's ``report.dns``
(``{error, at}``) until a later run succeeds; a run with nothing to change
calls nothing. It runs from the hourly ``hosted.tick`` and, through
``kick``, on a thread once a transaction that placed a server on a routed
host, deleted a server or changed a host's address has committed.
``_lock`` keeps it to one run at a time.

DNS is on while ``config.CF_API_TOKEN`` and ``CF_ZONE_ID`` are set and
hosting is (``HOSTED_DOMAIN``). Off, nothing is called, and placement skips
routed hosts (``fleet.place``): a server there would have no name. The
Cloudflare calls sit behind ``client()``, which tests replace with a fake
through ``set_client``.
"""

import ipaddress
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing

from . import config, db
from .log import log

API = "https://api.cloudflare.com/client/v4"
ERROR_MAX = 300                # the error kept in a server's report.dns, in characters

_lock = threading.Lock()       # one reconcile at a time: the tick's and a kick's can overlap


class DNSError(Exception):
    """Cloudflare could not be reached or refused the call. ``status`` is the
    HTTP status of its answer, 0 when there was none."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# --- the Cloudflare client ----------------------------------------------------

def _messages(answer) -> str:
    errors = answer.get("errors") if isinstance(answer, dict) else None
    return "; ".join(f"{e.get('code', '')} {e.get('message', '')}".strip() for e in errors or [] if isinstance(e, dict))


class CloudflareClient:
    """The DNS record calls on one zone, answering plain dicts."""

    def __init__(self, token: str, zone_id: str):
        self._token, self._zone = token, zone_id

    def _call(self, method: str, path: str = "", body: dict | None = None, query: dict | None = None):
        url = f"{API}/zones/{urllib.parse.quote(self._zone)}/dns_records{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {self._token}", "User-Agent": "gamma-cloud",
                                              "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                answer = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                detail = _messages(json.load(e))
            except ValueError:
                detail = ""
            raise DNSError(f"Cloudflare answered {e.code}" + (f": {detail}" if detail else ""), e.code) from e
        except (OSError, ValueError) as e:
            raise DNSError(f"Cloudflare could not be reached: {e}") from e
        if not isinstance(answer, dict) or not answer.get("success"):
            raise DNSError("Cloudflare refused the call: " + (_messages(answer) or "no reason given"))
        return answer.get("result")

    def find(self, name: str) -> list[dict]:
        """The zone's records with exactly this name."""
        return self._call("GET", query={"name": name, "per_page": 100}) or []

    def create(self, record: dict) -> dict:
        return self._call("POST", body=record)

    def update(self, record_id: str, record: dict) -> dict:
        return self._call("PATCH", "/" + urllib.parse.quote(record_id), body=record)

    def delete(self, record_id: str) -> None:
        self._call("DELETE", "/" + urllib.parse.quote(record_id))


_client = None


def client():
    """The client for ``config.CF_API_TOKEN`` and ``CF_ZONE_ID`` (or the test fake)."""
    global _client
    if _client is None:
        _client = CloudflareClient(config.CF_API_TOKEN, config.CF_ZONE_ID)
    return _client


def set_client(fake) -> None:
    """Tests: replace the client (None goes back to the real one)."""
    global _client
    _client = fake


def enabled() -> bool:
    """DNS is on: a token and a zone are configured, and hosting is on."""
    return bool(config.CF_API_TOKEN and config.CF_ZONE_ID and config.HOSTED_DOMAIN)


# --- what the zone should hold ------------------------------------------------

def name_of(label: str) -> str:
    return f"{label}{config.HOSTED_SUFFIX}.{config.HOSTED_DOMAIN}"


def record_for(server_id: str, label: str, address: str) -> dict:
    """The record a server on a routed host gets: proxied (Cloudflare holds
    the certificate and hides the address), TTL automatic."""
    kind = "AAAA" if ipaddress.ip_address(address).version == 6 else "A"
    return {"type": kind, "name": name_of(label), "content": address, "proxied": True, "ttl": 1,
            "comment": f"Gamma hosted server {server_id}"}


def wanted(row) -> str:
    """The address the server's record should point at: its host's public IP
    while the server is not deleted, else "" (no record)."""
    return (row["public_ip"] or "") if row["state"] != "deleted" else ""


def _in_step(row) -> bool:
    want = wanted(row)
    return (row["dns_target"] == want and bool(row["dns_record_id"])) if want else not row["dns_record_id"]


def _sync(row, want: str) -> tuple[str, str, str]:
    """Make one server's record what ``want`` says: ``(record id, target,
    what was done)``. A record already gone counts as deleted; one removed
    by hand is made again, and a record that already has the name (an
    earlier run whose answer was not stored, or one made by hand) is taken
    over rather than doubled."""
    c, record_id = client(), row["dns_record_id"]
    if not want:
        try:
            c.delete(record_id)
        except DNSError as e:
            if e.status != 404:
                raise
        return "", "", "deleted"
    record = record_for(row["id"], row["label"], want)
    if record_id:
        try:
            c.update(record_id, record)
            return record_id, want, "updated"
        except DNSError as e:
            if e.status != 404:
                raise
    found = [r for r in c.find(record["name"]) if r.get("type") in ("A", "AAAA") and r.get("id")]
    if found:
        c.update(found[0]["id"], record)
        return found[0]["id"], want, "updated"
    made = c.create(record)
    if not isinstance(made, dict) or not made.get("id"):
        raise DNSError("Cloudflare answered no record id")
    return made["id"], want, "created"


def _store(conn, server_id: str, record_id: str, target: str, error: str = "") -> None:
    """Write one server's record (or keep its error) in a short transaction."""
    db.begin_write(conn)
    row = conn.execute("SELECT report FROM hosted_servers WHERE id = ?", (server_id,)).fetchone()
    if row is not None:
        report = db.json_dict(row["report"])
        if error:
            report["dns"] = {"error": error[:ERROR_MAX], "at": db.now()}
        else:
            report.pop("dns", None)
        conn.execute("UPDATE hosted_servers SET dns_record_id = ?, dns_target = ?, report = ? WHERE id = ?",
                     (record_id, target, json.dumps(report), server_id))
    conn.commit()


_ROWS = ("SELECT s.id, s.label, s.state, s.dns_record_id, s.dns_target, s.report, h.public_ip "
         "FROM hosted_servers s LEFT JOIN hosts h ON h.id = s.host_id ORDER BY s.created_at")


def reconcile(conn, gone: tuple[str, ...] = ()) -> dict:
    """Make every server's record what the rows say. The caller's
    transaction is committed first: no call is made under the write lock.
    ``gone`` are ids of records whose server row was removed (a purged
    account): each one no row holds any more is deleted. Answers the counts
    of what was done."""
    done = {"created": 0, "updated": 0, "deleted": 0, "failed": 0}
    if not enabled():
        return done
    conn.commit()
    with _lock:
        db.begin_write(conn)          # a kick waits here until the transaction that kicked it commits
        rows = conn.execute(_ROWS).fetchall()
        conn.commit()
        for row in rows:
            if _in_step(row):
                if "dns" in db.json_dict(row["report"]):     # an error a later change made moot (the host lost its IP)
                    _store(conn, row["id"], row["dns_record_id"], row["dns_target"])
                continue
            want = wanted(row)
            try:
                record_id, target, what = _sync(row, want)
            except DNSError as e:
                done["failed"] += 1
                log.warning("dns: %s for %s failed: %s", name_of(row["label"]), row["id"], e)
                _store(conn, row["id"], row["dns_record_id"], row["dns_target"], str(e))
                continue
            done[what] += 1
            _store(conn, row["id"], record_id, target)
            log.info("dns: %s %s%s", what, name_of(row["label"]), f" -> {target}" if target else "")
        held = {r["dns_record_id"] for r in rows if r["dns_record_id"]}
        for record_id in gone:
            if not record_id or record_id in held:
                continue
            try:
                client().delete(record_id)
                done["deleted"] += 1
            except DNSError as e:
                if e.status != 404:
                    done["failed"] += 1
                    log.warning("dns: record %s of a removed server was not deleted (remove it by hand): %s",
                                record_id, e)
    return done


def _run(gone: tuple[str, ...]) -> None:
    try:
        with closing(db.connect()) as conn:
            reconcile(conn, gone)
    except Exception as e:  # noqa: BLE001 — on its own thread: the hourly tick tries again
        log.warning("dns: reconcile failed: %s", e)


def kick(gone: str = "") -> threading.Thread | None:
    """Reconcile on a thread, once the caller's transaction has committed:
    the thread's first step waits for the write lock the caller holds. The
    thread, or None while DNS is off. ``gone``: the record id of a server
    row the caller removes (``reconcile``)."""
    if not enabled():
        return None
    t = threading.Thread(target=_run, args=((gone,) if gone else (),), daemon=True, name="gamma-dns")
    t.start()
    return t

"""What the operator is told (docs/dev/hosted.md "Alerts"): the problems
that exist right now, derived from the state each time (``collect``), kept
in the ``alerts`` table so each is mailed once per opening (``sync``), and
one mail listing the new ones (``notify``).

An alert is keyed by what it is about (``job:<id>``, ``down:<server id>``,
...), so the same problem found again is the same row. A problem must
persist for its ``settle`` seconds before it is mailed: a container down
for one heartbeat is not worth a mail. A row whose problem is gone is
resolved; one that comes back opens again and is mailed again. An admin's
dismissal hides an open alert from the Overview and stops its mail until it
resolves.

``sync`` runs inside the caller's transaction (the hourly tick, every
heartbeat, every job result); the caller commits and then calls ``notify``
with what ``sync`` returned, so no mail goes out for a write that rolled
back.
"""

import threading
from contextlib import closing

from . import config, db, fleet, mail, settings
from .log import log

SETTLE = 10 * 60          # what a problem that may clear by itself waits before it is mailed
STUCK_AFTER = 3600        # a server placed on a host and still provisioning after this is stuck
FULL_MEMORY = 0.90        # of what can be placed on a host (its memory less the reserve)
FULL_DISK = 0.85
EVENT_DAYS = 7            # a webhook event older than this no longer raises an alert
KEEP_DAYS = 30            # a resolved alert is kept this long
UP_STATES = ("running", "grace", "read_only", "suspended")   # a server whose container should be up


def _alert(key: str, text: str, link: str, settle: int = 0) -> dict:
    return {"key": key, "kind": key.partition(":")[0], "text": text, "link": link, "settle": settle}


def _when(ts: str) -> str:
    return ts[:16].replace("T", " ") + " UTC"


def _hours(ts: str) -> int:
    return int((db.parse(db.now()) - db.parse(ts)).total_seconds() // 3600)


def _jobs(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT j.id, j.kind, j.payload, j.result, s.label FROM fleet_jobs j "
                          "LEFT JOIN hosted_servers s ON s.id = j.server_id "
                          "WHERE j.state = 'failed' AND j.kind != 'logs'").fetchall():
        # an orphan's removal has no server, only the label in its payload
        label = r["label"] or fleet.json_dict(r["payload"]).get("label") or "a host"
        error = str(fleet.json_dict(r["result"]).get("error") or "no error given")[:200]
        out.append(_alert(f"job:{r['id']}", f"{r['kind']} job for {label} failed: {error}", "#servers"))
    return out


def _servers(conn, public_ip: dict[str, str]) -> list[dict]:
    """A server waiting for a host, stuck provisioning on one, down or
    unhealthy, or without its DNS record (on a host with a public address)."""
    out = []
    stuck_since = db.after(-STUCK_AFTER)
    for r in conn.execute("SELECT id, label, state, host_id, report, state_changed_at, dns_target FROM hosted_servers "
                          "WHERE state != 'deleted'").fetchall():
        sid, label, report = r["id"], r["label"], fleet.json_dict(r["report"])
        if r["state"] == "provisioning" and not r["host_id"]:
            out.append(_alert(f"waiting:{sid}", f"{label} is waiting for a host with room", "#servers", SETTLE))
        elif r["state"] == "provisioning" and r["state_changed_at"] < stuck_since:
            hours = _hours(r["state_changed_at"])
            out.append(_alert(f"stuck:{sid}", f"{label} has been provisioning for {hours} hour"
                              + ("s" if hours != 1 else ""), "#servers"))
        agent = report.get("agent")
        if r["state"] in UP_STATES and isinstance(agent, dict) and agent.get("last_seen_at"):
            if not agent.get("running"):
                out.append(_alert(f"down:{sid}", f"{label} is down", "#servers", SETTLE))
            elif agent.get("health") == "unhealthy":
                out.append(_alert(f"down:{sid}", f"{label} is unhealthy", "#servers", SETTLE))
        ip = public_ip.get(r["host_id"])
        if ip and r["dns_target"] != ip:
            dns = report.get("dns")
            error = str(dns.get("error") or "")[:200] if isinstance(dns, dict) else ""
            out.append(_alert(f"dns:{sid}", f"{label} has no DNS record yet" + (f": {error}" if error else ""),
                              "#servers", SETTLE))
    return out


def _hosts(hosts: list[dict]) -> list[dict]:
    """A host silent past ``fleet.STALE_AFTER``, or close to full: its
    committed memory near what can be placed on it, or its disk."""
    out = []
    for h in hosts:
        if h["stale"] and h["last_seen_at"]:
            out.append(_alert(f"host_stale:{h['id']}", f"{h['name']} has not reported since {_when(h['last_seen_at'])}",
                              "#servers"))
        placeable = h["memory_mb"] - h["reserve_mb"]
        if placeable > 0 and h["committed_mb"] >= FULL_MEMORY * placeable:
            out.append(_alert(f"host_full:{h['id']}:memory", f"{h['name']} has {h['committed_mb']} of the "
                              f"{placeable} MB it can place committed to servers", "#servers"))
        if h["disk_mb"] > 0 and h["disk_used_mb"] >= FULL_DISK * h["disk_mb"]:
            out.append(_alert(f"host_full:{h['id']}:disk", f"{h['name']}'s disk is "
                              f"{round(h['disk_used_mb'] / h['disk_mb'] * 100)}% full", "#servers"))
    return out


def collect(conn) -> list[dict]:
    """The problems that exist now, each ``{key, kind, text, link,
    settle}``: ``link`` is the Admin tab that shows it, ``settle`` the
    seconds it must persist before it is mailed. A handful of reads."""
    hosts = fleet.hosts(conn)
    out = _jobs(conn) + _servers(conn, {h["id"]: h.get("public_ip") or "" for h in hosts}) + _hosts(hosts)
    for r in conn.execute("SELECT id, type FROM billing_events WHERE outcome IN ('mismatch', 'unknown') "
                          "AND received_at >= ?", (db.after(-EVENT_DAYS * 86400),)).fetchall():
        out.append(_alert(f"billing:{r['id']}", f"Stripe event {r['type']} could not be matched to an account",
                          "#billing"))
    if settings.unguarded_registration():
        out.append(_alert("signup:unguarded", "Registration is open and the anti-bot check is off", "#settings"))
    return out


def sync(conn) -> list[dict]:
    """Bring the ``alerts`` table to what ``collect`` finds, inside the
    caller's transaction: a new problem is a new row, an open one is
    touched (its text kept current), one no longer found is resolved, and a
    resolved one found again opens afresh (its marks cleared). Returns the
    alerts due for mail, marked mailed: open, not dismissed, not mailed,
    and settled. While alerts are off nothing is marked, so turning them on
    mails what is open then. The caller commits, then calls ``notify``.
    A failure here is logged and undone, never the caller's: a heartbeat or
    a job result must not be lost to it."""
    conn.execute("SAVEPOINT alerts_sync")
    try:
        due = _sync(conn)
    except Exception as e:  # noqa: BLE001
        conn.execute("ROLLBACK TO alerts_sync")
        log.warning("alerts: sync failed: %s", e)
        due = []
    conn.execute("RELEASE alerts_sync")
    return due


def _sync(conn) -> list[dict]:
    now = db.now()
    found = {a["key"]: a for a in collect(conn)}
    open_rows = {r["key"]: r for r in conn.execute("SELECT * FROM alerts WHERE resolved_at IS NULL").fetchall()}
    for key in open_rows.keys() - found.keys():
        conn.execute("UPDATE alerts SET resolved_at = ? WHERE key = ?", (now, key))
    due = []
    for key, a in found.items():
        row = open_rows.get(key)
        if row is None:
            conn.execute("INSERT INTO alerts (key, kind, text, link, first_at, last_at) VALUES (?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT (key) DO UPDATE SET kind = excluded.kind, text = excluded.text, "
                         "link = excluded.link, first_at = excluded.first_at, last_at = excluded.last_at, "
                         "mailed_at = NULL, resolved_at = NULL, dismissed_at = NULL",
                         (key, a["kind"], a["text"], a["link"], now, now))
            first_at, quiet = now, False
        else:
            conn.execute("UPDATE alerts SET text = ?, link = ?, last_at = ? WHERE key = ?",
                         (a["text"], a["link"], now, key))
            first_at, quiet = row["first_at"], bool(row["dismissed_at"] or row["mailed_at"])
        if not quiet and first_at <= db.after(-a["settle"]):
            due.append({k: a[k] for k in ("key", "kind", "text", "link")})
    if due and settings.alerts_on():
        conn.executemany("UPDATE alerts SET mailed_at = ? WHERE key = ?", [(now, a["key"]) for a in due])
        return due
    return []


def purge(conn, days: int = KEEP_DAYS) -> None:
    """Resolved alerts older than ``days`` go (from the hourly tick)."""
    conn.execute("DELETE FROM alerts WHERE resolved_at < ?", (db.after(-days * 86400),))


def listing(conn, resolved_days: int = 0) -> list[dict]:
    """The open alerts, oldest first, dismissed ones too (``dismissed_at``
    says so); with ``resolved_days``, also those resolved within that many
    days, after them."""
    rows = conn.execute("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY first_at, key").fetchall()
    if resolved_days:
        rows += conn.execute("SELECT * FROM alerts WHERE resolved_at >= ? ORDER BY resolved_at DESC",
                             (db.after(-resolved_days * 86400),)).fetchall()
    return [dict(r) for r in rows]


def dismiss(conn, key: str, actor: str) -> dict:
    """Hide an open alert from the Overview, and from mail, until it
    resolves and comes back. Raises ``LookupError`` for no open alert."""
    if not conn.execute("UPDATE alerts SET dismissed_at = ? WHERE key = ? AND resolved_at IS NULL",
                        (db.now(), key)).rowcount:
        raise LookupError(key)
    db.audit(conn, "alert.dismiss", actor=actor, detail=key)
    return dict(conn.execute("SELECT * FROM alerts WHERE key = ?", (key,)).fetchone())


# --- the mail -----------------------------------------------------------------

def recipients() -> list[str]:
    """``settings.alert_email``, else every admin account's confirmed address."""
    if settings.alert_email():
        return [settings.alert_email()]
    with closing(db.connect()) as conn:
        return [r[0] for r in conn.execute("SELECT email FROM accounts WHERE is_admin = 1 AND deleted_at IS NULL "
                                           "AND email_verified_at IS NOT NULL AND email != '' ORDER BY created_at")]


def _message(alerts: list[dict]) -> tuple[str, str, str]:
    """(subject, text, html) of one mail listing ``alerts``, each with a
    link to its Admin tab."""
    subject = (f"Gamma Cloud: {alerts[0]['text']}" if len(alerts) == 1
               else f"Gamma Cloud: {len(alerts)} things need attention")
    lines = [f"{a['text']}: {config.PUBLIC_URL}/admin{a['link']}" for a in alerts]
    text, html = mail.compose("Hello,", lines, ("Open the Admin page", config.PUBLIC_URL + "/admin"),
                              "Alerts are turned off, or sent to another address, on the Admin page's Settings tab.")
    return subject, text, html


def _send(to: str, subject: str, text: str, html: str) -> None:
    try:
        mail.send(to, subject, text, html)
    except mail.MailError as e:
        log.warning("alert mail to %s failed: %s", to, e)


def notify(alerts: list[dict]) -> None:
    """One mail listing ``alerts`` to each recipient, after the caller's
    commit. SMTP goes out on a thread, so a heartbeat or a job result never
    waits on it; a failure is logged. Nothing while alerts are off."""
    if not alerts or not settings.alerts_on():
        return
    subject, text, html = _message(alerts)
    to = recipients()
    if not to:
        log.warning("%d alert(s) and no address to send them to: %s", len(alerts), subject)
    for address in to:
        if config.MAIL_BACKEND == "smtp":
            threading.Thread(target=_send, args=(address, subject, text, html), daemon=True).start()
        else:
            _send(address, subject, text, html)


def send_test() -> list[str]:
    """The Settings tab's *Send test alert*: an alert mail built and
    addressed the way ``notify`` does it, sent now (whether alerts are on
    or off), so a failure reaches the admin. The addresses; raises
    ``mail.MailError`` as ``mail.send`` does, and ``LookupError`` when
    there is no address."""
    to = recipients()
    if not to:
        raise LookupError("no address")
    subject, text, html = _message([_alert("test", "Test alert from the Admin page", "#settings")])
    for address in to:
        mail.send(address, subject, text, html)
    return to

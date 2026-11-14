"""What this server trusts when it connects over HTTPS (docs/dev/https.md).

``use_system_store`` makes every outbound connection verify against the
operating system's trust store (truststore), so the server trusts what the
desktop window trusts: a root in the macOS Keychain or Windows' store, an
intermediate Windows fetches (AIA). net_proxy.install calls it at start;
the mobile apps keep certifi's bundle, where truststore does not run.

The desktop app's server also keeps the certificates its shell was told to
trust for one origin (``tls_pins`` in users.db, the shell's list, replaced
whole by ``replace_pins``): a NAS's self-signed certificate, or one a
private authority signed that this computer's store does not hold. A pin
is the SHA-256 of the leaf certificate's DER. net_proxy's HTTPS connection
to a pinned origin takes the certificate whose digest is the pin, and
nothing else verifies there: another certificate must verify normally
(the NAS moved to a public certificate), or the connection fails with
``CertificateChanged`` before a request byte is sent. Never on a hosted
server (``enabled``), never for a host under the Gamma Cloud's domain
(``cloud_domain``), never in the guarded fetches (net_guard): a pin is the
person's trust in a server they connected to, not in a link someone pastes.
"""

import hashlib
import ipaddress
import os
import re
import ssl
import sys
import threading
import urllib.parse

from . import config, db
from .logbuf import log

MAX_PINS = 200
TEXT_MAX = 500                                   # the longest subject, issuer or date a pin keeps
DEFAULT_CLOUD_ISSUER = "https://account.gammapdf.com"   # as the shell's sidecar.js sets it
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _plain_class():
    """ssl.SSLContext as the ssl module defines it, even when truststore
    was injected first: the class in ssl.SSLContext's MRO whose __module__
    is "ssl". A server-side context, and the pinned path's, must not be
    truststore's: it verifies the peer's chain whatever the context says."""
    return next(cls for cls in ssl.SSLContext.__mro__ if cls.__module__ == "ssl")


_PLAIN_CONTEXT = _plain_class()   # captured at import: this is the module that injects truststore
_said: set = set()                # what use_system_store and the pinned path logged, each once
_pins_cache: tuple = ("", {})     # (the users.db it was read from, {origin: sha256})
_pins_lock = threading.Lock()


def _say(line: str) -> None:
    if line not in _said:
        _said.add(line)
        log.info(line)


def use_system_store() -> str:
    """Verify outbound HTTPS against the operating system's trust store
    (truststore: Windows CryptoAPI, macOS Security framework, Linux
    OpenSSL's paths), so the server trusts what the desktop window trusts.
    → "system" | "certifi" (mobile, where truststore does not run) |
    "off" (GAMMA_TRUSTSTORE=0, read when this runs: at start) | "missing"
    (not installed: a source checkout)."""
    if sys.platform in ("ios", "android") or hasattr(sys, "getandroidapilevel"):
        return "certifi"
    if os.environ.get("GAMMA_TRUSTSTORE", "").strip().lower() in ("0", "false", "no", "off"):
        _say("[startup] GAMMA_TRUSTSTORE=0: HTTPS is verified with OpenSSL's certificate authorities")
        return "off"
    try:
        import truststore
    except ImportError:
        _say("[startup] truststore is not installed: HTTPS is verified with OpenSSL's certificate authorities")
        return "missing"
    truststore.inject_into_ssl()
    _say("[startup] verifying HTTPS with the system's trust store")
    return "system"


# --- pins ----------------------------------------------------------------------------

def enabled() -> bool:
    """Whether this server keeps pins: the desktop app's own (GAMMA_FOLDER_LINKS),
    never a hosted one."""
    return config.folder_links_enabled() and not config.hosted()


def cloud_domain() -> str:
    """The Gamma Cloud's domain, where no pin is kept or applied: the
    account server's host without its first label (account.gammapdf.com →
    gammapdf.com; hosted servers are ``<label>-user.`` under it). The
    issuer is GAMMA_CLOUD_ISSUER, else the desktop default
    (GAMMA_CLOUD_DEFAULT_ISSUER, else account.gammapdf.com). A two-label
    host is the domain itself; an IP address or a single label (a test's
    account server) names none: ""."""
    env = config.cloud_env()
    host = (urllib.parse.urlsplit(env["issuer"] or env["default_issuer"] or DEFAULT_CLOUD_ISSUER).hostname or "")
    host = host.rstrip(".")
    if not host or "." not in host or _is_ip(host):
        return ""
    labels = host.split(".")
    return ".".join(labels[1:]) if len(labels) > 2 else host


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def under_cloud(host: str) -> bool:
    """Whether ``host`` is the Gamma Cloud's domain or a name under it."""
    domain, host = cloud_domain(), host.lower().strip("[]").rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def origin_of(host: str, port: int | None = None) -> str:
    """``https://host[:port]`` in a URL's origin form: the host lowercase,
    an IDN in punycode, IPv6 bracketed, the port left out when 443.
    ValueError for a host that is none."""
    host = host.strip().lower().strip("[]")
    if not host:
        raise ValueError("no host")
    try:
        ip = ipaddress.ip_address(host)
        host = f"[{ip.compressed}]" if ip.version == 6 else str(ip)
    except ValueError:
        try:
            host = host if host.isascii() else host.encode("idna").decode("ascii")
        except UnicodeError as e:
            raise ValueError(f"{host[:80]} is not a host name") from e
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def canonical_origin(url: str) -> str:
    """The origin of an https:// address as ``origin_of`` writes it
    (``HTTPS://NAS:443/x`` → ``https://nas``); ValueError for another
    scheme, a sign-in in it, or no host."""
    text = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise ValueError(f"{text[:80]} is not an address") from e
    if parts.scheme.lower() != "https":
        raise ValueError(f"{text[:80]}: a certificate is trusted only for an https:// address")
    if "@" in parts.netloc or not parts.hostname:
        raise ValueError(f"{text[:80]} is not a server's address")
    return origin_of(parts.hostname, port)


def pins() -> list[dict]:
    """The pins kept: [{origin, sha256, subject, issuer, not_before,
    not_after, pinned_at}], by origin."""
    with db.connect_users_db() as conn:
        rows = conn.execute("SELECT origin, sha256, subject, issuer, not_before, not_after, pinned_at "
                            "FROM tls_pins ORDER BY origin").fetchall()
    keys = ("origin", "sha256", "subject", "issuer", "not_before", "not_after", "pinned_at")
    return [dict(zip(keys, row)) for row in rows]


def replace_pins(rows: list[dict]) -> list[dict]:
    """The shell's whole list in place of the kept one, in one transaction:
    each row's origin made canonical (https only, never under the Cloud's
    domain, each once), its sha256 64 lowercase hex digits, its subject,
    issuer and dates strings of at most TEXT_MAX characters, MAX_PINS rows
    at most. ``pinned_at`` is kept for a pin that did not change, now for
    the rest. ValueError with words for a list that does not do (nothing
    is replaced then). → ``pins()``."""
    if not isinstance(rows, list):
        raise ValueError("pins must be a list")
    if len(rows) > MAX_PINS:
        raise ValueError(f"at most {MAX_PINS} certificates can be trusted")
    clean = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each pin must be an object")
        origin = canonical_origin(row.get("origin"))
        if under_cloud(urllib.parse.urlsplit(origin).hostname or ""):
            raise ValueError(f"{origin} is a Gamma Cloud server: its certificate is never pinned")
        if origin in clean:
            raise ValueError(f"{origin} is listed twice")
        sha256 = row.get("sha256")
        if not isinstance(sha256, str) or not _SHA256.match(sha256):
            raise ValueError(f"{origin}: sha256 must be 64 lowercase hex digits")
        texts = {}
        for key in ("subject", "issuer", "not_before", "not_after"):
            value = row.get(key, "")
            if not isinstance(value, str) or len(value) > TEXT_MAX:
                raise ValueError(f"{origin}: {key} must be text of at most {TEXT_MAX} characters")
            texts[key] = value
        clean[origin] = {"sha256": sha256, **texts}
    now = db.page_now()
    with db.connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        kept = {origin: (sha256, at) for origin, sha256, at in
                conn.execute("SELECT origin, sha256, pinned_at FROM tls_pins")}
        conn.execute("DELETE FROM tls_pins")
        conn.executemany(
            "INSERT INTO tls_pins (origin, sha256, subject, issuer, not_before, not_after, pinned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(origin, p["sha256"], p["subject"], p["issuer"], p["not_before"], p["not_after"],
              kept[origin][1] if kept.get(origin, ("",))[0] == p["sha256"] else now)
             for origin, p in clean.items()])
        conn.commit()
    global _pins_cache
    with _pins_lock:
        _pins_cache = ("", {})
    return pins()


def _pinned() -> dict:
    """{origin: sha256} of the kept pins, read once per users.db."""
    global _pins_cache
    where = str(db.USERS_DB)
    cached = _pins_cache
    if cached[0] == where:
        return cached[1]
    with _pins_lock:
        if _pins_cache[0] != where:
            _pins_cache = (where, {p["origin"]: p["sha256"] for p in pins()})
        return _pins_cache[1]


def pin_for(host: str, port: int | None) -> str:
    """The pinned digest of https://host[:port]; "" for none, not enabled,
    or a host under cloud_domain(). Kept in memory, read again after
    replace_pins."""
    if not enabled() or not host or under_cloud(host):
        return ""
    try:
        origin = origin_of(host, port)
    except ValueError:
        return ""
    return _pinned().get(origin, "")


def plain_context(protocol, **values) -> ssl.SSLContext:
    """A context of the plain class (``_PLAIN_CONTEXT``), with ``values``
    (check_hostname, verify_mode, verify_flags, minimum_version, in that
    order) set through its base class: ssl.SSLContext's own setters look
    ssl.SSLContext up by name, which with truststore injected is
    truststore's class, and would call themselves without end."""
    context = _PLAIN_CONTEXT(protocol)
    base = super(_PLAIN_CONTEXT, _PLAIN_CONTEXT)
    for name, value in values.items():
        getattr(base, name).__set__(context, value)
    return context


def pin_context() -> ssl.SSLContext:
    """The context of a connection to a pinned origin: no chain verified,
    no host name checked (the digest is the check, made before a request
    byte is sent), TLS 1.2 at least. Never truststore's (``_PLAIN_CONTEXT``)."""
    return plain_context(ssl.PROTOCOL_TLS_CLIENT, check_hostname=False, verify_mode=ssl.CERT_NONE,
                         minimum_version=ssl.TLSVersion.TLSv1_2)


def describe(der: bytes) -> dict:
    """What a certificate says of itself, for the shell's card and a link's
    status: {sha256, subject, issuer, not_before, not_after}, the names as
    RFC 4514 strings and the dates ISO 8601, each at most TEXT_MAX
    characters ("" for what cannot be read)."""
    seen = {"sha256": hashlib.sha256(der).hexdigest(), "subject": "", "issuer": "", "not_before": "",
            "not_after": ""}
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        seen.update(subject=cert.subject.rfc4514_string()[:TEXT_MAX], issuer=cert.issuer.rfc4514_string()[:TEXT_MAX],
                    not_before=cert.not_valid_before_utc.isoformat()[:TEXT_MAX],
                    not_after=cert.not_valid_after_utc.isoformat()[:TEXT_MAX])
    except Exception:  # noqa: BLE001 — a certificate this cannot read is still described by its digest
        pass
    return seen


def log_replaced(origin: str) -> None:
    """Said once when a pinned origin serves another certificate that verifies."""
    _say(f"[tls] the pinned certificate of {origin} is no longer served; the one it serves now verifies")


class CertificateChanged(ssl.SSLCertVerificationError):
    """The pinned origin answered with another certificate that does not
    verify either. ``origin``, ``seen`` (``describe`` of it, None when the
    server sent none), ``changed = True``.
    str: "The certificate of <host> changed"."""

    changed = True

    def __init__(self, origin: str, seen: dict | None):
        host = urllib.parse.urlsplit(origin).hostname or origin
        super().__init__(f"The certificate of {host} changed")
        self.origin, self.seen = origin, seen
        self.verify_code, self.verify_message = None, "the certificate changed"

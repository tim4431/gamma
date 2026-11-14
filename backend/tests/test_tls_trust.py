"""What the server trusts over HTTPS (gamma/tls_trust.py, net_proxy's
pinned connection): the system trust store, and the certificates the
desktop app's shell was told to trust, against a real TLS server on
127.0.0.1 with certificates made here. No certificate authority of this
computer is asked, except in the one case that tries truststore: every
other verifies with a context of the plain class that loads only the test
authority, when it needs one."""

import datetime
import functools
import hashlib
import json
import logging
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from gamma import gamma_sync, net_guard, net_proxy, tls_trust

NOW = datetime.datetime.now(datetime.timezone.utc)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(cn, *, issuer=None, ca=False, aki=True, critical=True):
    """(certificate, key): self-signed unless ``issuer`` (a (cert, key)),
    with SANs 127.0.0.1 and localhost for a leaf."""
    import ipaddress

    key = ec.generate_private_key(ec.SECP256R1())
    signer_cert, signer_key = issuer or (None, key)
    builder = (x509.CertificateBuilder().subject_name(_name(cn))
               .issuer_name(signer_cert.subject if signer_cert else _name(cn))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(NOW - datetime.timedelta(days=1)).not_valid_after(NOW + datetime.timedelta(days=30))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=critical)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if aki:
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
            (signer_cert.public_key() if signer_cert else key.public_key())), critical=False)
    if not ca:
        builder = builder.add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
    return builder.sign(signer_key, hashes.SHA256()), key


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _digest(cert):
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


@pytest.fixture(scope="module")
def certs():
    ca = _cert("Test CA", ca=True)
    # what 3.13's strict mode refuses: an authority whose basicConstraints is not
    # critical, a leaf without an Authority Key Identifier
    loose_ca = _cert("Loose CA", ca=True, aki=False, critical=False)
    return {"leaf": _cert("leaf"), "other": _cert("other"), "ca": ca, "signed": _cert("signed", issuer=ca),
            "loose_ca": loose_ca, "loose": _cert("loose", issuer=loose_ca, aki=False)}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append(self.path)
        body = json.dumps({"workspace": {"id": "w"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def tls(tmp_path):
    """``tls(cert, key)``: a server on 127.0.0.1 over TLS with that
    certificate (a server context of the plain class); ``url``, ``seen``."""
    servers = []

    def start(cert, key):
        certfile, keyfile = tmp_path / f"{len(servers)}.crt", tmp_path / f"{len(servers)}.key"
        certfile.write_bytes(_pem(cert))
        keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        context = tls_trust._PLAIN_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads, server.seen = True, []
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.url = f"https://127.0.0.1:{server.server_address[1]}"
        server.origin = server.url
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def verifying(monkeypatch, tmp_path):
    """``verifying(ca=None, strict=False)``: the routed opener's verifying
    context is one of the plain class trusting only ``ca`` (none: nothing),
    and gamma_sync goes through that opener."""
    def use(ca=None, strict=False):
        context = tls_trust.plain_context(ssl.PROTOCOL_TLS_CLIENT)
        if strict:
            context = tls_trust.plain_context(ssl.PROTOCOL_TLS_CLIENT,
                                              verify_flags=context.verify_flags | ssl.VERIFY_X509_STRICT)
        if ca is not None:
            path = tmp_path / "ca.pem"
            path.write_bytes(_pem(ca))
            context.load_verify_locations(path)
        monkeypatch.setattr(net_proxy, "RoutedHTTPSHandler", functools.partial(net_proxy.RoutedHTTPSHandler,
                                                                               context=context))
        monkeypatch.setattr(gamma_sync, "make_opener", net_proxy.build_opener)
        monkeypatch.setattr(gamma_sync, "_opener", (None, None))

    use()
    return use


@pytest.fixture
def desktop(monkeypatch):
    """The desktop app's own server, with no pins yet."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.delenv("GAMMA_HOSTED", raising=False)
    tls_trust.replace_pins([])
    yield
    tls_trust.replace_pins([])


def _pin(origin, sha256):
    return tls_trust.replace_pins([{"origin": origin, "sha256": sha256, "subject": "CN=x"}])


def _whoami(url):
    return gamma_sync.Server(url, "secret", timeout=5).get_json("/api/sync/whoami")


# --- the system trust store ----------------------------------------------------------

@pytest.fixture
def trust_state(monkeypatch):
    """The trust state on entry (truststore injected or not) is the one on
    exit, so no later test runs in another. Without truststore (the mobile
    apps' pins, requirements-mobile.txt) these tests have nothing to check."""
    truststore = pytest.importorskip("truststore")

    injected = ssl.SSLContext is truststore.SSLContext
    truststore.extract_from_ssl()
    monkeypatch.delenv("GAMMA_TRUSTSTORE", raising=False)
    yield truststore
    (truststore.inject_into_ssl if injected else truststore.extract_from_ssl)()


def test_the_system_store_is_used(trust_state):
    assert tls_trust.use_system_store() == "system"
    assert ssl.SSLContext is trust_state.SSLContext
    assert tls_trust._PLAIN_CONTEXT.__module__ == "ssl" and tls_trust._plain_class() is tls_trust._PLAIN_CONTEXT
    assert not isinstance(tls_trust.pin_context(), trust_state.SSLContext)


def test_the_mobile_apps_keep_certifi(trust_state, monkeypatch):
    monkeypatch.setattr(sys, "platform", "android")
    assert tls_trust.use_system_store() == "certifi"
    assert ssl.SSLContext is not trust_state.SSLContext


def test_the_system_store_can_be_turned_off(trust_state, monkeypatch):
    monkeypatch.setenv("GAMMA_TRUSTSTORE", "0")
    assert tls_trust.use_system_store() == "off"
    assert ssl.SSLContext is not trust_state.SSLContext


def test_without_truststore_it_is_missing(trust_state, monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)
    assert tls_trust.use_system_store() == "missing"


def test_the_pinned_path_has_a_floor():
    context = tls_trust.pin_context()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_NONE and context.check_hostname is False


# --- pins ----------------------------------------------------------------------------

def test_an_unpinned_certificate_is_refused_and_described(tls, certs, verifying, desktop):
    server = tls(*certs["leaf"])
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert not e.value.changed and e.value.seen["sha256"] == _digest(certs["leaf"][0])
    assert e.value.seen["subject"] == "CN=leaf" and e.value.seen["not_after"].startswith(str(NOW.year))
    assert gamma_sync.status_fields(e.value)["error_kind"] == "certificate"
    assert server.seen == []


def test_an_unpinned_certificate_is_refused_by_the_system_store(tls, certs, desktop, monkeypatch):
    """The one case that verifies with truststore, as the app does."""
    truststore = pytest.importorskip("truststore")

    assert ssl.SSLContext is truststore.SSLContext   # the app injected it (net_proxy.install)
    monkeypatch.setattr(gamma_sync, "make_opener", net_proxy.build_opener)
    monkeypatch.setattr(gamma_sync, "_opener", (None, None))
    server = tls(*certs["leaf"])
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert e.value.seen["sha256"] == _digest(certs["leaf"][0]) and server.seen == []


def test_a_pinned_certificate_is_taken(tls, certs, verifying, desktop):
    server = tls(*certs["leaf"])
    _pin(server.origin, _digest(certs["leaf"][0]))
    assert _whoami(server.url) == {"workspace": {"id": "w"}}
    assert server.seen == ["/api/sync/whoami"]


def test_another_certificate_than_the_pin_is_changed(tls, certs, verifying, desktop):
    server = tls(*certs["other"])
    _pin(server.origin, _digest(certs["leaf"][0]))
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert e.value.changed and e.value.seen["sha256"] == _digest(certs["other"][0])
    assert str(e.value) == "The certificate of 127.0.0.1 changed"
    assert gamma_sync.status_fields(e.value) == {"error_kind": "certificate_changed", "cert": e.value.seen}
    assert server.seen == []   # refused before a request byte, the bearer included


@pytest.mark.parametrize("env", [{"GAMMA_HOSTED": "1"}, {"GAMMA_FOLDER_LINKS": ""}])
def test_a_pin_counts_only_on_the_desktop_apps_server(tls, certs, verifying, desktop, monkeypatch, env):
    server = tls(*certs["leaf"])
    _pin(server.origin, _digest(certs["leaf"][0]))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert not tls_trust.enabled() and tls_trust.pin_for("127.0.0.1", server.server_address[1]) == ""
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert not e.value.changed and e.value.seen is None   # nor is it looked at


def test_the_guarded_opener_ignores_pins(tls, certs, verifying, desktop, monkeypatch):
    server = tls(*certs["leaf"])
    _pin(server.origin, _digest(certs["leaf"][0]))
    monkeypatch.setattr(gamma_sync, "make_opener", functools.partial(net_proxy.build_opener, cert_pins=False))
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert not e.value.changed

    # and the guarded fetches (a pasted link, an AI or web fetch) build their opener so
    built = []

    class Opened:
        def open(self, req, timeout=None):
            return "opened"

    def build_opener(*handlers, **kwargs):
        built.append(kwargs)
        return Opened()

    monkeypatch.setattr(net_guard, "validate_public_url", lambda url, policy=None: None)
    monkeypatch.setattr(net_proxy, "build_opener", build_opener)
    assert net_guard.guarded_urlopen(server.url + "/paper.pdf") == "opened"
    assert [kwargs.get("cert_pins") for kwargs in built] == [False]


def test_a_pin_is_never_held_to_the_strict_flags(tls, certs, verifying, desktop):
    server = tls(*certs["loose"])
    verifying(ca=certs["loose_ca"][0])
    assert _whoami(server.url) == {"workspace": {"id": "w"}}
    verifying(ca=certs["loose_ca"][0], strict=True)
    with pytest.raises(gamma_sync.CertificateRefused):
        _whoami(server.url)
    _pin(server.origin, _digest(certs["loose"][0]))
    assert _whoami(server.url) == {"workspace": {"id": "w"}}


def test_a_stale_pin_gives_way_to_a_chain_that_verifies(tls, certs, verifying, desktop, caplog):
    server = tls(*certs["signed"])
    verifying(ca=certs["ca"][0])
    _pin(server.origin, _digest(certs["leaf"][0]))
    with caplog.at_level(logging.INFO, logger="gamma"):
        assert _whoami(server.url) == {"workspace": {"id": "w"}}
        assert _whoami(server.url) == {"workspace": {"id": "w"}}
    said = [r.message for r in caplog.records if r.message.startswith("[tls]")]
    assert said == [f"[tls] the pinned certificate of {server.origin} is no longer served; the one it serves now verifies"]


def test_no_certificate_is_a_mismatch(tls, certs, verifying, desktop, monkeypatch):
    server = tls(*certs["leaf"])
    _pin(server.origin, _digest(certs["leaf"][0]))
    real = ssl.SSLSocket.getpeercert
    monkeypatch.setattr(ssl.SSLSocket, "getpeercert",
                        lambda self, binary_form=False: None if binary_form else real(self, binary_form))
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert e.value.changed and e.value.seen is None and server.seen == []


def test_a_leaf_sent_alone_is_worded_as_a_missing_intermediate(tls, certs, verifying, desktop):
    server = tls(*certs["signed"])
    with pytest.raises(gamma_sync.CertificateRefused) as e:
        _whoami(server.url)
    assert "fullchain.pem" in str(e.value)
    # Python 3.13 tells how many certificates the server sent
    # (SSLSocket.get_unverified_chain); before it, as in the desktop app's
    # 3.12, the words name both causes (CertificateRefused)
    assert e.value.missing_intermediate == hasattr(ssl.SSLSocket, "get_unverified_chain")


def test_the_pins_are_replaced_whole(desktop):
    first = tls_trust.replace_pins([{"origin": "HTTPS://NAS:443/x", "sha256": "a" * 64, "subject": "CN=nas"},
                                    {"origin": "https://[FD00::5]:5001", "sha256": "b" * 64}])
    assert [(p["origin"], p["sha256"], p["subject"]) for p in first] == [
        ("https://[fd00::5]:5001", "b" * 64, ""), ("https://nas", "a" * 64, "CN=nas")]
    assert tls_trust.pin_for("nas", 443) == "a" * 64 and tls_trust.pin_for("fd00::5", 5001) == "b" * 64
    assert tls_trust.pin_for("nas", 5001) == ""
    # an unchanged pin keeps when it was made; a changed one is made now
    second = {p["origin"]: p for p in tls_trust.replace_pins([
        {"origin": "https://nas", "sha256": "a" * 64}, {"origin": "https://[fd00::5]:5001", "sha256": "c" * 64}])}
    assert second["https://nas"]["pinned_at"] == first[1]["pinned_at"]
    assert tls_trust.pin_for("fd00::5", 5001) == "c" * 64
    tls_trust.replace_pins([])
    assert tls_trust.pins() == [] and tls_trust.pin_for("nas", None) == ""


@pytest.mark.parametrize("rows, words", [
    ([{"origin": "http://nas", "sha256": "a" * 64}], "only for an https:// address"),
    ([{"origin": "https://user@nas", "sha256": "a" * 64}], "is not a server's address"),
    ([{"origin": "https://nas", "sha256": "A" * 64}], "64 lowercase hex digits"),
    ([{"origin": "https://nas", "sha256": "a" * 63}], "64 lowercase hex digits"),
    ([{"origin": "https://bob-user.gammapdf.com", "sha256": "a" * 64}], "is a Gamma Cloud server"),
    ([{"origin": "https://gammapdf.com", "sha256": "a" * 64}], "is a Gamma Cloud server"),
    ([{"origin": "https://nas", "sha256": "a" * 64}, {"origin": "https://NAS:443", "sha256": "b" * 64}], "twice"),
    ([{"origin": "https://nas", "sha256": "a" * 64, "subject": "x" * 501}], "at most 500"),
    ([{"origin": f"https://nas{i}", "sha256": "a" * 64} for i in range(tls_trust.MAX_PINS + 1)], "at most 200"),
    ("https://nas", "must be a list"),
], ids=["http", "sign-in", "upper case", "short", "hosted", "cloud domain", "twice", "long subject", "too many",
        "not a list"])
def test_a_list_that_does_not_do_replaces_nothing(desktop, rows, words):
    tls_trust.replace_pins([{"origin": "https://kept", "sha256": "d" * 64}])
    with pytest.raises(ValueError, match=words):
        tls_trust.replace_pins(rows)
    assert [p["origin"] for p in tls_trust.pins()] == ["https://kept"]


def test_the_cloud_domain_is_the_issuers_without_its_first_label(monkeypatch):
    for issuer, domain in (("", "gammapdf.com"), ("https://account.example.org", "example.org"),
                           ("https://example.org", "example.org"), ("http://127.0.0.1:9002", ""),
                           ("http://localhost:9002", "")):
        monkeypatch.setenv("GAMMA_CLOUD_ISSUER", issuer)
        monkeypatch.delenv("GAMMA_CLOUD_DEFAULT_ISSUER", raising=False)
        assert tls_trust.cloud_domain() == domain
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", "")
    assert tls_trust.under_cloud("Bob-User.GammaPDF.com.") and not tls_trust.under_cloud("notgammapdf.com")


def test_a_certificate_is_described_within_bounds(certs):
    long_cert, _ = _cert("x" * 64)
    seen = tls_trust.describe(long_cert.public_bytes(serialization.Encoding.DER))
    assert set(seen) == {"sha256", "subject", "issuer", "not_before", "not_after"}
    assert all(len(v) <= tls_trust.TEXT_MAX for v in seen.values())
    assert tls_trust.describe(b"not a certificate")["subject"] == ""

"""Where a host is (gamma/host_scope.py): ``literal`` against the cases the
desktop shell's hostScope.js reads too (tests/data/host_scope_cases.json),
and ``resolved`` with a stand-in resolver."""

import json
import socket
import threading
from pathlib import Path

import pytest

from gamma import host_scope
from gamma.proxy_settings import DEFAULT_BYPASS

CASES = json.loads((Path(__file__).parent / "data" / "host_scope_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["host"] for c in CASES])
def test_literal_reads_the_shared_cases(case):
    assert host_scope.literal(case["host"]) == case["literal"]


def test_an_ipv4_mapped_address_is_read_as_ipv4():
    assert host_scope.literal("::ffff:192.168.1.5") == "lan"
    assert host_scope.literal("::ffff:8.8.8.8") == "far"


def test_the_table_is_the_proxys_local_list():
    """Every net, suffix and loopback entry is in DEFAULT_BYPASS; *.localhost
    is the one entry the scope adds."""
    bypass = {entry.strip() for entry in DEFAULT_BYPASS.split(",")}
    assert {str(net) for net in host_scope.LAN_NETS} <= bypass
    assert {"*" + suffix for suffix in host_scope.LAN_SUFFIXES} <= bypass
    assert {"localhost", "127.0.0.0/8", "::1"} <= bypass
    assert "*.localhost" not in bypass   # the addition


@pytest.fixture
def resolver(monkeypatch):
    """``{name: [addresses]}`` as getaddrinfo answers them; a name missing
    raises gaierror, a callable answer is called (it may wait)."""
    answers = {}

    def resolve(host, port, *args, **kwargs):
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        found = answers[host]() if callable(answers[host]) else answers[host]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in found]

    monkeypatch.setattr(host_scope, "_resolved", {})
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return answers


def test_a_name_is_where_it_resolves(resolver):
    resolver.update({"nas.example.com": ["192.168.1.5"], "nas.lan": ["203.0.113.5"],
                     "split.example.com": ["192.168.1.5", "203.0.113.5"], "me.example.com": ["127.0.0.1"],
                     "tail.ts.net": ["100.101.102.103"], "v6.example.com": ["fd00::5"]})
    assert host_scope.resolved("nas.example.com") == ("lan", False)
    assert host_scope.resolved("nas.lan") == ("far", False)   # a public answer outweighs the suffix
    assert host_scope.resolved("split.example.com") == ("far", False)
    assert host_scope.resolved("me.example.com") == ("loopback", False)
    assert host_scope.resolved("tail.ts.net") == ("lan", False)
    assert host_scope.resolved("v6.example.com") == ("lan", False)


def test_a_dual_stack_lan_host_is_lan(resolver):
    # a home router's DNS or mDNS answers a NAS's private IPv4 address and its global IPv6 one
    resolver.update({"diskstation.local": ["192.168.1.10", "2003:e1:ab00::10"],
                     "diskstation": ["2003:e1:ab00::10", "192.168.1.10"],
                     "split.example.com": ["192.168.1.10", "203.0.113.5", "2003:e1:ab00::10"],
                     "v6only.example.com": ["2003:e1:ab00::10"]})
    assert host_scope.resolved("diskstation.local") == ("lan", False)
    assert host_scope.resolved("diskstation") == ("lan", False)
    assert host_scope.resolved("split.example.com") == ("far", False)   # a public IPv4 address is still far
    assert host_scope.resolved("v6only.example.com") == ("far", False)  # a global IPv6 address alone too


def test_a_fake_ip_answer_counts_as_none(resolver):
    resolver.update({"nas.lan": ["198.18.0.5"], "example.com": ["198.18.0.6"]})
    assert host_scope.resolved("nas.lan") == ("lan", False)
    assert host_scope.resolved("example.com") == ("far", False)


def test_a_name_that_does_not_resolve_is_its_literal_a_dotted_one_far(resolver):
    assert host_scope.resolved("nas.lan") == ("lan", False)
    assert host_scope.resolved("nas") == ("lan", False)
    assert host_scope.resolved("example.com") == ("far", False)


def test_an_address_or_localhost_asks_no_resolver(resolver, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("resolved")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert host_scope.resolved("192.168.1.5") == ("lan", False)
    assert host_scope.resolved("[2606:4700::1111]") == ("far", False)
    assert host_scope.resolved("localhost") == ("loopback", False)


def test_the_suites_resolver_guard_counts_as_not_resolving(monkeypatch):
    # conftest's guard raises AssertionError for any name but loopback's
    monkeypatch.setattr(host_scope, "_resolved", {})
    assert host_scope.resolved("nas.example.com") == ("far", False)
    assert host_scope.resolved("nas.home.arpa") == ("lan", False)


def test_a_slow_resolver_is_lan_and_unsure_until_it_answers(resolver):
    release = threading.Event()
    resolver["slow.example.com"] = lambda: (release.wait(5), ["203.0.113.5"])[1]
    try:
        assert host_scope.resolved("slow.example.com", timeout=0.05) == ("lan", True)
    finally:
        release.set()
    for _ in range(100):   # the lookup, still running, keeps its answer when it comes
        if "slow.example.com" in host_scope._resolved:
            break
        threading.Event().wait(0.02)
    assert host_scope.resolved("slow.example.com", timeout=0.05) == ("far", False)


def test_an_answer_is_kept(resolver):
    resolver["nas.example.com"] = ["192.168.1.5"]
    assert host_scope.resolved("nas.example.com") == ("lan", False)
    resolver["nas.example.com"] = ["203.0.113.5"]
    assert host_scope.resolved("NAS.example.com.") == ("lan", False)

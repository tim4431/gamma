"""The server's network proxy setting (Settings → Administration → Network
proxy, docs/dev/network_proxy.md): how this server reaches the internet.
The routing itself is gamma/net_proxy.py.

Saved as one JSON object under the users.db ``settings`` key ``proxy``:
``{mode, server, username, password, bypass, proxy_dns}``, the password
sealed with the data directory's key (``publisher_sessions.seal``) and the
server without its sign-in. ``mode`` is ``system`` (the default: the
environment's or the OS's proxy, as before there was a setting), ``none``
or ``manual``; ``bypass`` is the "Don't use the proxy for" list
(``parse_bypass``), DEFAULT_BYPASS until an admin saves another;
``proxy_dns`` lets the proxy resolve the names of the guarded fetches
(gamma/net_guard.py). A hosted container's network is its operator's: the
setting reads as ``system`` there and cannot be saved (``ManagedError``).

``policy`` is what every outbound connection routes by, read as it opens
and parsed again only when the saved value changed, so a save applies at
once. ``public`` is the admin API's view (the password never), ``save``
stores fields, ``test`` tries a draft against a few destinations without
saving it.
"""

import dataclasses
import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import InvalidToken

from . import config, net_guard, net_proxy
from .logbuf import log
from .net_proxy import Policy, ProxyServer, Rule
from .publisher_sessions import seal, unseal
from .server_settings import _peek_raw, _set_raw

SETTINGS_KEY = "proxy"                   # users.db ``settings``: the saved setting, as JSON
MODES = ("system", "none", "manual")
FIELDS = ("mode", "server", "username", "password", "bypass", "proxy_dns")
EXAMPLE = "http://127.0.0.1:7890"
DEFAULT_BYPASS = ("localhost, 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, "
                  "169.254.0.0/16, fc00::/7, fe80::/10, *.local, *.lan, *.home.arpa, *.internal, <local>")
BYPASS_MAX = 20000                       # the longest bypass list, in characters
TEST_TIMEOUT_S = 10
# The Test's destinations, (name, url, guarded): a guarded one is fetched
# through net_guard, as a link a person or the assistant opens.
TEST_TARGETS = (("OpenAI", "https://api.openai.com/v1/models", False),
                ("ChatGPT", "https://chatgpt.com/backend-api/codex/models", False),
                ("Anthropic", "https://api.anthropic.com/v1/models", False),
                ("Zotero", "https://api.zotero.org/", False),
                ("arXiv", "https://arxiv.org/", True))
SYSTEM = Policy()
_SCHEMES = ("http", "socks5", "socks5h")
_NAME_RE = re.compile(r"[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?(?:\.[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?)*")

_save_lock = threading.Lock()
_parsed: tuple = (None, SYSTEM)          # (the saved value ``policy`` read last, its Policy)


class ManagedError(ValueError):
    """Gamma Cloud runs this server: its network is the operator's."""


# --- parsing -------------------------------------------------------------------------

def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _check_sign_in(scheme: str, username: str, password: str) -> None:
    if any(ord(c) < 32 or ord(c) == 127 for c in username + password):
        raise ValueError("The proxy's user name and password can't hold control characters")
    most = 255 if scheme != "http" else 1024  # SOCKS5 spells each length in one byte
    if max(len(username.encode()), len(password.encode())) > most:
        raise ValueError(f"The proxy's user name and password take at most {most} bytes each")


def parse_server(text: str, *, require_port: bool = True) -> ProxyServer:
    """The proxy ``text`` names: ``scheme://[user:password@]host:port`` with
    scheme http, socks5 or socks5h, a bare ``host:port`` being http. A
    sign-in in it, percent-decoded, moves into the fields. A system proxy
    (``require_port`` False) without a port takes its scheme's. ValueError
    with what to write instead, never echoing the text."""
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise ValueError(f"Enter the proxy's address, as in {EXAMPLE}")
    unreadable = f"That isn't a proxy address: write it as in {EXAMPLE}"
    if len(text) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        raise ValueError(unreadable)
    try:
        url = urllib.parse.urlsplit(text if "://" in text else "http://" + text)
        scheme, host, port = url.scheme, url.hostname or "", url.port
    except ValueError:
        raise ValueError(unreadable) from None
    if not url.netloc:  # also: the text did not start with a scheme
        raise ValueError(unreadable)
    if scheme == "https":
        raise ValueError("An https:// proxy isn't supported yet: use the proxy's http:// or socks5:// port")
    if scheme not in _SCHEMES:
        raise ValueError(f"A {scheme}:// proxy isn't supported: use an http:// or socks5:// one")
    if url.path not in ("", "/") or url.query or url.fragment or text.endswith(("?", "#")):
        raise ValueError(f"Give the proxy's address alone, without a path, as in {EXAMPLE}")
    if not host or "%" in host or not (_is_ip(host) or _NAME_RE.fullmatch(host)):
        raise ValueError(unreadable)
    if not port:
        if require_port or port == 0:
            raise ValueError(f"Add the proxy's port, as in {EXAMPLE}")
        port = 80 if scheme == "http" else 1080
    username, password = urllib.parse.unquote(url.username or ""), urllib.parse.unquote(url.password or "")
    _check_sign_in(scheme, username, password)
    return ProxyServer(scheme, host, port, username, password)


def _rule(item: str) -> Rule | None:
    if item in ("*", "<local>"):
        return Rule("all" if item == "*" else "local", None, item)
    bare = item[1:-1] if item.startswith("[") and item.endswith("]") else item
    try:
        ip = ipaddress.ip_address(bare)
        return Rule("ip", ip, str(ip))
    except ValueError:
        pass
    if "/" in bare:
        try:
            net = ipaddress.ip_network(bare, strict=False)
            return Rule("net", net, str(net))
        except ValueError:
            return None
    name = item[2:] if item.startswith("*.") else item[1:] if item.startswith(".") else item
    return Rule("name", name, item) if _NAME_RE.fullmatch(name) else None


def parse_bypass(text: str) -> tuple:
    """The "Don't use the proxy for" rules ``text`` lists, split on commas
    and white space: ``*`` (everything), ``<local>`` (a name without a
    dot), an IP address, a range (``10.0.0.0/8``), or a name, where
    ``example.com``, ``.example.com`` and ``*.example.com`` each match the
    name and its subdomains. Lowercased, without a trailing dot, each once.
    ValueError naming the first entry that is none of these."""
    if len(text) > BYPASS_MAX:
        raise ValueError(f"The list of hosts that don't use the proxy takes at most {BYPASS_MAX} characters")
    rules = {}
    for entry in filter(None, re.split(r"[\s,]+", text)):
        item = entry.lower()
        rule = _rule(item[:-1] if item.endswith(".") and len(item) > 1 else item)
        if rule is None:
            raise ValueError(f"“{entry[:100]}” isn’t a host, domain, IP address or IP range")
        rules.setdefault(rule.text, rule)
    return tuple(rules.values())


# --- the saved setting ---------------------------------------------------------------

def _stored(raw: str) -> dict:
    """A saved value read defensively: what is not as ``save`` writes it
    reads as the default, a password that no longer unseals (the data
    directory's key changed) as ""."""
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}

    def text(name: str, default: str = "") -> str:
        found = value.get(name, default)
        return found if isinstance(found, str) else default

    try:
        password = unseal(text("password"))
    except (InvalidToken, ValueError, OSError):
        log.warning("[proxy] the saved proxy password cannot be decrypted (the data directory's key changed?)")
        password = ""
    return {"mode": text("mode") if text("mode") in MODES else "system", "server": text("server"),
            "username": text("username"), "password": password, "bypass": text("bypass", DEFAULT_BYPASS),
            "proxy_dns": value.get("proxy_dns") is True}


def _raw() -> str:
    """The saved value, "" when there is none, read with
    ``server_settings._peek_raw`` (as offsite._kv), which never creates
    users.db: ``policy`` runs wherever a connection opens, manage.py's
    commands included, and a restore (into an empty data directory) must
    find users.db as it was and nothing holding it."""
    return _peek_raw(SETTINGS_KEY)


def _saved() -> dict:
    return _stored(_raw())


def _policy_of(conf: dict) -> Policy:
    """The Policy of a saved or checked setting; ``system`` on a hosted
    container. ValueError when its server or bypass list does not parse."""
    if config.hosted():
        return SYSTEM
    if conf["mode"] == "system":
        return Policy("system", proxy_dns=conf["proxy_dns"])
    if conf["mode"] == "none":
        return Policy("none")
    server = dataclasses.replace(parse_server(conf["server"]), username=conf["username"],
                                 password=conf["password"])
    return Policy("manual", server, parse_bypass(conf["bypass"]), conf["proxy_dns"])


def policy() -> Policy:
    """What every outbound connection routes by (net_proxy.route), read as
    it opens: the saved setting, or ``system`` where it cannot be read or
    parsed and on a hosted container."""
    global _parsed
    if config.hosted():
        return SYSTEM
    try:
        raw = _raw()
    except (sqlite3.Error, OSError):  # an unreadable users.db never stops a connection
        return SYSTEM
    seen, parsed = _parsed
    if raw != seen:
        try:
            parsed = _policy_of(_stored(raw))
        except ValueError:
            parsed = SYSTEM
        _parsed = (raw, parsed)
    return parsed


def public() -> dict:
    """The admin API's view: ``{mode, server, username, password_set,
    bypass, default_bypass, proxy_dns, managed, system}`` — the password
    never, only whether one is set; ``bypass`` the saved list
    (DEFAULT_BYPASS until one is saved); ``managed`` "hosted" where Gamma
    Cloud runs the server (the mode is ``system`` there), else None;
    ``system`` what that mode finds (net_proxy.system_view)."""
    conf, hosted = _saved(), config.hosted()
    return {"mode": "system" if hosted else conf["mode"], "server": conf["server"], "username": conf["username"],
            "password_set": bool(conf["password"]), "bypass": conf["bypass"], "default_bypass": DEFAULT_BYPASS,
            "proxy_dns": conf["proxy_dns"] and not hosted, "managed": "hosted" if hosted else None,
            "system": net_proxy.system_view()}


def _same_sign_in(current: dict, server: ProxyServer, username: str) -> bool:
    """``server`` is the saved proxy's host (whatever its port or scheme)
    and ``username`` the saved user name."""
    try:
        saved = parse_server(current["server"])
    except ValueError:
        return False
    return saved.host.lower() == server.host.lower() and username == current["username"]


def _checked(current: dict, fields: dict) -> dict:
    """``fields`` (any of FIELDS) laid over ``current`` and checked, as
    saved: the server without its sign-in, which moves into the username
    and, when it holds one, the password; a password absent or empty keeps
    ``current``'s for the same proxy host and user name (another one asks
    for it again), and goes with the username when that is emptied; a
    password needs a user name; the bypass list normalized. ValueError
    with the reason a person can act on."""
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(unknown)}.")
    conf = {**current, **{k: v for k, v in fields.items() if v is not None and k != "password"}}
    if any(not isinstance(conf[k], str) for k in ("mode", "server", "username", "bypass")):
        raise ValueError("The proxy's mode, address, user name and bypass list must be text.")
    if not isinstance(conf["proxy_dns"], bool):
        raise ValueError("proxy_dns must be true or false.")
    if conf["mode"] not in MODES:
        raise ValueError("The proxy setting must be system, none or manual.")
    password = fields.get("password")
    if password is not None and not isinstance(password, str):
        raise ValueError("The proxy's password must be text.")
    conf["username"] = conf["username"].strip()
    server = parse_server(conf["server"]) if conf["server"].strip() else None
    conf["server"] = server.url() if server else ""
    if server and server.username:
        conf["username"] = server.username
    typed = (server.password if server else "") or password or ""
    if typed:
        if not conf["username"]:
            raise ValueError("Add the user name this password goes with.")
        conf["password"] = typed
    elif not conf["username"]:
        conf["password"] = ""
    elif server and conf["password"] and not _same_sign_in(current, server, conf["username"]):
        # the saved password goes only to the proxy and the user name it was typed for
        raise ValueError("Type the proxy's password again: the proxy or its user name changed.")
    _check_sign_in(server.scheme if server else "http", conf["username"], conf["password"])
    if conf["mode"] == "manual" and not server:
        raise ValueError(f"Enter the proxy's address, as in {EXAMPLE}")
    conf["bypass"] = ", ".join(rule.text for rule in parse_bypass(conf["bypass"]))
    return {k: conf[k] for k in FIELDS}


def save(fields: dict) -> dict:
    """Store ``fields`` (any of FIELDS, ``_checked``) over the saved
    setting, the password sealed; ``public()`` after it. Connections opened
    from then on route by it. ManagedError on a hosted container,
    ValueError when a field is wrong (nothing saved)."""
    if config.hosted():
        raise ManagedError("Gamma Cloud manages this server's network.")
    with _save_lock:
        conf = _checked(_saved(), fields)
        _set_raw(SETTINGS_KEY, json.dumps({**conf, "password": seal(conf["password"])}))
    return public()


# --- the Test ------------------------------------------------------------------------

def _test_url(url: str) -> str:
    """``url`` when the Test can try it: http(s), a host, no sign-in."""
    url = url.strip()
    try:
        parts = urllib.parse.urlsplit(url)
        ok = (parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
              and parts.username is None and len(url) <= 2048 and not any(c.isspace() for c in url))
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("Test an http:// or https:// address, as in https://api.deepseek.com")
    return url


def _failure(e: Exception, host: str) -> str:
    """A Test line's error in words; never a sign-in, which no text here holds."""
    if isinstance(e, net_guard.BlockedUrlError):
        return f"Blocked: {e.reason}"
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(reason, net_proxy.ProxyError):
        return str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ("The certificate wasn't trusted: a proxy that inspects HTTPS needs its certificate installed "
                "on this server")
    if isinstance(reason, TimeoutError):
        return f"Timed out after {TEST_TIMEOUT_S} s"
    if isinstance(reason, socket.gaierror):
        return f"Couldn't look up {host}"
    return (str(reason) or type(reason).__name__)[:200]


def _try(trial: Policy, name: str, url: str, guarded: bool) -> dict:
    """One line of the Test: the route ``url`` takes, then one GET of it
    through the trusted opener, or net_guard's when ``guarded``."""
    parts = urllib.parse.urlsplit(url)
    result = {"name": name, "url": url, "route": "direct", "via": "", "reason": "", "ok": False, "status": 0,
              "ms": 0, "error": ""}
    started = time.monotonic()
    try:
        way = net_proxy.route(parts.hostname or "", parts.scheme, trial, parts.port)
        result.update(route="direct" if way.kind == "direct" else "proxy", reason=way.reason,
                      via="" if way.kind == "direct" else way.server.url())
        with (net_guard.guarded_urlopen(url, timeout=TEST_TIMEOUT_S, policy=trial) if guarded
              else net_proxy.build_opener(policy=trial).open(url, timeout=TEST_TIMEOUT_S)) as response:
            result["status"] = response.status
    except urllib.error.HTTPError as e:  # an answer all the same, unless it refuses this network
        result["status"] = e.code
        refused = net_proxy.refused_network(e)
        if refused:
            result["error"] = f"{e.code}: {refused}"
        e.close()
    except Exception as e:  # noqa: BLE001 — every failure is a line of the Test
        result["error"] = _failure(e, parts.hostname or "")
    result.update(ok=not result["error"], ms=round((time.monotonic() - started) * 1000))
    return result


def test(fields: dict | None = None, url: str = "") -> dict:
    """The pane's Test: the saved setting, or ``fields`` laid over it as
    ``save`` would (nothing saved), tried in parallel against Gamma Cloud
    (when an issuer is configured), TEST_TARGETS and ``url`` (an http or
    https address, when given). ``{results: [{name, url, route, via,
    reason, ok, status, ms, error}]}``, any HTTP answer counting as reached
    but one that refuses this server's network (net_proxy.refused_network).
    ValueError for a draft or an address that cannot be tried."""
    from . import cloud_auth  # local: only the Test needs the issuer

    trial = _policy_of(_checked(_saved(), fields or {}))
    targets = list(TEST_TARGETS)
    cloud = cloud_auth.settings()
    if cloud["enabled"] and cloud["issuer"]:
        targets.insert(0, ("Gamma Cloud", cloud["issuer"], False))
    if url:
        targets.append(("", _test_url(url), False))
    with ThreadPoolExecutor(len(targets)) as pool:
        return {"results": list(pool.map(lambda target: _try(trial, *target), targets))}

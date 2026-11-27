"""The server's cloud connections (docs/dev/backups.md "Cloud
destinations"): an admin signs in to Google Drive or OneDrive in Settings →
Backups, and the backup tasks that copy there send it their snapshots
(gamma/snapshot_copies.py). Like the bucket, each is the server's, not an
account's. One ``CloudAccount`` per cloud keeps it; ``CLOUDS`` describes
each (``Cloud``) and ``ACCOUNTS`` holds them by the store's key (``drive``,
``onedrive``), which the routes (``/api/admin/clouds/{store}``), the tasks
and the jobs name a store by.

A connection is made through Gamma's own OAuth app of that cloud
(``config.GOOGLE_DRIVE_CLIENT`` and ``GOOGLE_DRIVE_DESKTOP_CLIENT``,
``config.ONEDRIVE_CLIENT_ID``), so connecting is signing in and allowing
it: nobody sets anything up. A server may use an app of its own instead,
saved from the command line (``manage.py cloud-client <store> [--desktop]``,
``set_client``; users.db ``settings`` keys ``gdrive_client`` /
``gdrive_desktop_client`` and ``onedrive_client``, a secret encrypted),
which the browser suite does for its stand-in services. A token works only
through the client it was made through (``_client_of``), so a change of
client ends a connection made through the old one and any code waiting.

Connecting takes one of two ways (gamma/cloud_oauth.py):

- **The service's page** (``start_browser`` / ``finish_browser``), when
  Gamma is opened on the server's own computer (the desktop app, a
  loopback address): the browser goes to the service's authorization page
  and comes back to the server's callback with a code, traded with its
  PKCE verifier (``_browser``, in memory, by state, ``BROWSER_TTL``).
- **A code** (the device flow, ``start`` / ``poll``) anywhere else: ``start``
  asks the service for a code, which the pane shows with the service's
  address; the person enters it there, on any device, and ``poll``, which
  the pane calls while the code is shown, asks at most once per the
  interval the service gave (``_pending``, in memory: one connection at a
  time, gone with the process or once the code expired). Nothing polls in
  the background: a code nobody enters just expires.

Once the service says yes, the account's address is read and the connection
saved in users.db (the cloud's ``settings_key``): the refresh token,
encrypted with the data directory's key as the bucket's secret key is, the
address and name, the folder's path, when, and the client ID it was made
through. Access tokens are renewed as the store's client asks
(``_access``, one held in memory per cloud); a service that rotates refresh
tokens (Microsoft) has the new one saved in place of the old.

``disconnect`` revokes the token where the service can (Google; Microsoft
has no way, so the person removes the app from their account) and forgets
it; the copies stay. ``set_folder`` changes the folder new copies go to (the
old one stays where it is), a path of folder names from the cloud's top
(``check_folder``); ``browse`` and ``make_folder`` are the folder
browser's, from that top.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from re import Pattern
from typing import Callable

from cryptography.fernet import InvalidToken

from . import cloud_http, cloud_oauth, config, gdrive, onedrive
from .backup_store import Store, check_key
from .cloud_http import CloudConfigError, CloudError, CloudUnreachable
from .logbuf import log
from .publisher_sessions import seal, unseal
from .server_settings import _peek_raw, _set_raw

FOLDER_MAX = 120                 # a folder's name
FOLDER_DEPTH = 16                # the names in its path: a walk asks the service once a name
CLIENT_FIELD_MAX = 200
BROWSER_TTL = 600


@dataclass(frozen=True)
class Cloud:
    """A cloud a connection can be made to. ``key``: the store's key;
    ``name`` what messages call it; ``settings_key`` the connection's
    users.db key; ``client_key`` / ``desktop_key`` those of a client of the
    server's own for the code / for the page ("" when one client does both);
    ``gammas()`` Gamma's own ``(code client, page client)``, each ``(id,
    secret)``; ``make(auth, folder, attempts=)`` the store's client;
    ``loopback_host`` the host the page sends the browser back to ("" the
    one Gamma was opened at; Microsoft matches the registered
    ``localhost``); ``bad_chars`` what a folder's name may not hold beside
    a backslash and control characters."""
    key: str
    name: str
    service: cloud_oauth.Service
    settings_key: str
    client_key: str
    desktop_key: str
    gammas: Callable[[], tuple[tuple[str, str], tuple[str, str]]]
    make: Callable[..., Store]
    client_id: Pattern
    client_id_hint: str
    needs_secret: bool = True
    default_folder: str = "Gamma backups"
    loopback_host: str = ""
    bad_chars: str = ""


CLOUDS = (
    Cloud(key="drive", name="Google Drive", service=gdrive.GOOGLE, settings_key="gdrive",
          client_key="gdrive_client", desktop_key="gdrive_desktop_client",
          gammas=lambda: (config.GOOGLE_DRIVE_CLIENT, config.GOOGLE_DRIVE_DESKTOP_CLIENT), make=gdrive.Client,
          client_id=gdrive.CLIENT_ID_RE, client_id_hint="A Google client ID is one word ending in .apps.googleusercontent.com."),
    Cloud(key="onedrive", name="OneDrive", service=onedrive.MICROSOFT, settings_key="onedrive",
          client_key="onedrive_client", desktop_key="",
          gammas=lambda: ((config.ONEDRIVE_CLIENT_ID, ""), (config.ONEDRIVE_CLIENT_ID, "")), make=onedrive.Client,
          client_id=onedrive.CLIENT_ID_RE, client_id_hint="A Microsoft application (client) ID is a GUID.",
          needs_secret=False, loopback_host="localhost", bad_chars='"*:<>?|'),
)


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CloudAccount:
    """The server's connection to one cloud."""

    def __init__(self, cloud: Cloud):
        self.cloud = cloud
        self.key = cloud.key
        self.name = cloud.name
        self._lock = threading.Lock()        # the saved connection and the sign-ins under way
        self._token_lock = threading.Lock()  # one renewal at a time
        # The device flow in progress: {device_code, user_code, verification_url,
        # expires_at, interval, next_poll_at, folder, oauth}; None between connections.
        self._pending: dict | None = None
        self._last_error = ""  # why the last connection did not complete, which ``status`` shows
        # Sign-ins on the service's page under way: state → {verifier, redirect_uri, folder, oauth, expires_at}.
        self._browser: dict[str, dict] = {}
        self._held: tuple[str, str, float] | None = None  # (refresh token's digest, access token, monotonic expiry)

    @property
    def not_connected(self) -> str:
        return f"{self.name} is not connected: an admin connects it in Settings → Backups"

    @property
    def no_app(self) -> str:
        return (f"This build of Gamma has no app to sign in to {self.name} with: an admin can give the server one "
                f"with manage.py cloud-client {self.key}")

    # --- what is saved ---

    def _read(self, key: str) -> dict:
        """A saved record; {} when there is none or it cannot be read."""
        try:
            raw = _peek_raw(key)
            value = json.loads(raw) if raw else {}
        except (sqlite3.Error, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _unseal(self, sealed, what: str) -> str:
        """A saved secret decrypted; "" when there is none or it no longer
        decrypts (the data directory's key changed)."""
        try:
            return unseal(sealed) if isinstance(sealed, str) else ""
        except (InvalidToken, ValueError, OSError):
            log.warning(f"[{self.key}] the saved {what} cannot be decrypted (the data directory's key changed?)")
            return ""

    def _saved(self) -> dict:
        """The saved connection, the refresh token decrypted ("" when it no
        longer decrypts); {} when there is none."""
        value = self._read(self.cloud.settings_key)
        if value:
            value["refresh_token"] = self._unseal(value.get("refresh_token"), "token")
        return value

    def _save(self, conn: dict) -> None:
        _set_raw(self.cloud.settings_key, json.dumps({**conn, "refresh_token": seal(conn["refresh_token"])}))

    # --- the OAuth client ---

    def _usable(self, client_id, secret) -> bool:
        return isinstance(client_id, str) and bool(client_id) and (bool(secret) or not self.cloud.needs_secret)

    def _client(self, key: str, gamma: tuple[str, str]) -> cloud_oauth.OAuthClient | None:
        """The server's own client saved under ``key``, else Gamma's; None
        when there is neither."""
        saved = self._read(key) if key else {}
        client_id = saved.get("client_id")
        secret = self._unseal(saved.get("client_secret"), "client secret") if saved.get("client_secret") else ""
        if self._usable(client_id, secret):
            return cloud_oauth.OAuthClient(client_id, secret)
        return cloud_oauth.OAuthClient(*gamma) if self._usable(*gamma) else None

    def oauth_client(self) -> cloud_oauth.OAuthClient | None:
        """The client a code is asked through."""
        return self._client(self.cloud.client_key, self.cloud.gammas()[0])

    def browser_client(self) -> cloud_oauth.OAuthClient | None:
        """The client of the service's page, on the server's own computer
        (the code's own where one client does both)."""
        if not self.cloud.desktop_key:
            return self.oauth_client()
        return self._client(self.cloud.desktop_key, self.cloud.gammas()[1])

    def _client_of(self, conn: dict) -> cloud_oauth.OAuthClient | None:
        """The client a connection was made through, among those this server
        has; None when it has it no longer."""
        for oauth in (self.oauth_client(), self.browser_client()):
            if oauth is not None and oauth.id == conn.get("client_id"):
                return oauth
        return None

    def check_client(self, client_id, secret="") -> cloud_oauth.OAuthClient:
        """A client given on the command line, trimmed. ValueError for one
        that cannot be."""
        if not isinstance(client_id, str) or not isinstance(secret, str):
            raise ValueError("The client ID and secret must be text.")
        client_id, secret = client_id.strip(), secret.strip()
        if not client_id:
            raise ValueError("Give the client ID.")
        if not self.cloud.client_id.fullmatch(client_id):
            raise ValueError(self.cloud.client_id_hint)
        if self.cloud.needs_secret and not secret:
            raise ValueError("Give the client secret.")
        if len(secret) > CLIENT_FIELD_MAX or any(c.isspace() for c in secret):
            raise ValueError("The client secret is one word, as the service shows it.")
        return cloud_oauth.OAuthClient(client_id, secret)

    def set_client(self, client_id, secret="", *, desktop: bool = False) -> dict:
        """Save a client of the server's own, used instead of Gamma's for the
        code (or, ``desktop``, for the page): manage.py cloud-client.
        ValueError for one that cannot be. The new ``status``."""
        key = self._client_key(desktop)
        oauth = self.check_client(client_id, secret)
        with self._lock:
            _set_raw(key, json.dumps({"client_id": oauth.id, "client_secret": seal(oauth.secret) if oauth.secret else ""}))
        self._client_changed()
        return self.status()

    def remove_client(self, *, desktop: bool = False) -> dict:
        """Forget the server's own client: Gamma's is used again, and a
        connection made through the one forgotten is disconnected."""
        key = self._client_key(desktop)
        with self._lock:
            _set_raw(key, "")
        self._client_changed()
        return self.status()

    def _client_key(self, desktop: bool) -> str:
        if desktop and not self.cloud.desktop_key:
            raise ValueError(f"{self.name} signs in with one client, the code's and the page's alike.")
        return self.cloud.desktop_key if desktop else self.cloud.client_key

    def _client_changed(self) -> None:
        """After the client changed: a code waiting was asked for through the
        old one and ends; a connection made through another than the ones
        now in force can no longer be renewed and is disconnected."""
        with self._lock:
            self._end()
        conn = self._saved()
        if conn.get("refresh_token") and self._client_of(conn) is None:
            self.disconnect()

    # --- the connection ---

    def _problem(self, conn: dict) -> str:
        """Why a saved connection cannot be used now, "" when it can."""
        if not conn.get("refresh_token"):
            return self.not_connected
        if self.oauth_client() is None and self.browser_client() is None:
            return self.no_app
        if self._client_of(conn) is None:
            return f"{self.name} was connected through another OAuth client: connect it again"
        return ""

    def ready(self) -> bool:
        """Whether a connection that can be used is saved."""
        return not self._problem(self._saved())

    def _access(self, renew: bool = False) -> str:
        """The connection's access token, renewed when it has less than a
        minute left or ``renew``; a rotated refresh token is saved in place
        of the old. CloudConfigError when there is no connection that can be
        used, or the service no longer honours it."""
        with self._token_lock:
            conn = self._saved()
            problem = self._problem(conn)
            if problem:
                raise CloudConfigError(problem)
            token = conn["refresh_token"]
            held = self._held
            if held and held[0] == _digest(token) and not renew and held[2] > time.monotonic() + 60:
                return held[1]
            access, lifetime, kept = cloud_oauth.refresh(
                self.cloud.service, self._client_of(conn), token,
                again=f"connect {self.name} again in Settings → Backups")
            if kept != token:
                with self._lock:
                    now = self._saved()
                    if now.get("refresh_token") == token:
                        self._save({**now, "refresh_token": kept})
            self._held = (_digest(kept), access, time.monotonic() + lifetime)
            return access

    def client(self, folder: str | None = None, **kwargs) -> Store:
        """A client for the saved connection's folder, or for ``folder`` (""
        the cloud's top). CloudConfigError when there is none that can be
        used."""
        conn = self._saved()
        problem = self._problem(conn)
        if problem:
            raise CloudConfigError(problem)
        if folder is None:
            folder = conn.get("folder", self.cloud.default_folder)
        return self.cloud.make(self._access, folder, **kwargs)

    def check_folder(self, folder) -> str:
        """The folder's path from the cloud's top: its names joined by ``/``,
        each trimmed (an empty one dropped), at most FOLDER_MAX characters
        and a key's segment (``backup_store.check_key``), none holding the
        cloud's ``bad_chars`` or (OneDrive) ending in a dot, at most
        FOLDER_DEPTH of them, one at least: never the top itself.
        ValueError otherwise."""
        if not isinstance(folder, str):
            raise ValueError("The folder must be text.")
        names = [name for name in (" ".join(part.split()) for part in folder.split("/")) if name]
        if not names:
            raise ValueError(f"Choose a folder in {self.name}.")
        try:
            if len(names) > FOLDER_DEPTH or any(len(name) > FOLDER_MAX for name in names):
                raise ValueError
            if self.cloud.bad_chars and any(c in self.cloud.bad_chars or name.endswith(".")
                                            for name in names for c in name):
                raise ValueError
            return check_key("/".join(names))
        except ValueError:
            bad = f", {' '.join(self.cloud.bad_chars)} or a dot at the end" if self.cloud.bad_chars else ""
            raise ValueError(f"Each folder's name must be at most {FOLDER_MAX} characters, not . or .., without \\{bad} "
                             f"or control characters, at most {FOLDER_DEPTH} folders deep.") from None

    def browse(self, path: str) -> dict:
        """The folder browser's listing of the folder ``path`` from the
        cloud's top ("" the top itself), tried as a call the pane waits on.
        ValueError for a path that cannot be a folder, CloudConfigError with
        nothing to use, CloudError when the service refuses."""
        return self.client("", attempts=cloud_http.INTERACTIVE_ATTEMPTS).browse(self.check_folder(path) if path else "")

    def make_folder(self, path: str) -> dict:
        """Make the folder ``path`` from the cloud's top (the folder
        browser's New folder): ``{path}``, the path as made. Errors as
        ``browse``'s."""
        path = self.check_folder(path)
        self.client("", attempts=cloud_http.INTERACTIVE_ATTEMPTS).make_folder(path)
        return {"path": path}

    def status(self) -> dict:
        """What the pane shows: ``{available, connected, email, name,
        folder, connected_at, problem, pending, error, revokes}``.
        ``available``: there is an app to sign in with; ``problem``: why the
        saved connection cannot be used ("" when it can, or there is none);
        ``pending``: the code to enter, ``{user_code, verification_url,
        expires_at, interval}``, while a connection waits; ``error``: why
        the last one did not complete, until the next one starts or Cancel;
        ``revokes``: whether Disconnect revokes the access at the
        service."""
        conn = self._saved()
        with self._lock:
            pending = self._pending
            if pending and pending["expires_at"] <= time.time():
                self._end("The code expired before it was entered: connect again.")
                pending = None
            error = self._last_error
        connected = bool(conn.get("refresh_token"))
        return {"available": self.oauth_client() is not None or self.browser_client() is not None,
                "connected": connected, "email": str(conn.get("email") or ""), "name": str(conn.get("name") or ""),
                "folder": str(conn.get("folder", self.cloud.default_folder)), "connected_at": conn.get("connected_at"),
                "problem": self._problem(conn) if connected else "",
                "pending": {"user_code": pending["user_code"], "verification_url": pending["verification_url"],
                            "expires_at": _iso(pending["expires_at"]), "interval": pending["interval"]}
                if pending else None,
                "error": error, "revokes": self.cloud.service.revoke_url is not None}

    def _end(self, error: str = "") -> None:
        """End the device flow in progress (with ``_lock`` held), keeping why
        for ``status``."""
        self._pending, self._last_error = None, error

    def start(self, folder: str | None = None) -> dict:
        """Start connecting: a code from the service for the pane to show (a
        new one replaces any in progress). ValueError for a folder that
        cannot be; CloudConfigError without a client; CloudError when the
        service gives no code. The new ``status``."""
        folder = self.check_folder(self.cloud.default_folder if folder is None else folder)
        oauth = self.oauth_client()
        if oauth is None:
            raise CloudConfigError(self.no_app)
        code = cloud_oauth.start_device_flow(self.cloud.service, oauth)
        now = time.time()
        with self._lock:
            self._end()
            self._pending = {**code, "folder": folder, "oauth": oauth, "expires_at": now + code["expires_in"],
                             "next_poll_at": now + code["interval"]}
        return self.status()

    def cancel(self) -> dict:
        with self._lock:
            self._end()
        return self.status()

    def start_browser(self, folder: str | None, redirect_uri: str) -> str:
        """Start signing in on the service's page, which comes back to
        ``redirect_uri`` (the server's callback at a loopback address): the
        page's address. ValueError for a folder that cannot be;
        CloudConfigError without a client for the page."""
        folder = self.check_folder(self.cloud.default_folder if folder is None else folder)
        oauth = self.browser_client()
        if oauth is None:
            raise CloudConfigError(self.no_app)
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        now = time.time()
        with self._lock:
            self._end()
            for old in [k for k, v in self._browser.items() if v["expires_at"] <= now]:
                del self._browser[old]
            self._browser[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "folder": folder,
                                    "oauth": oauth, "expires_at": now + BROWSER_TTL}
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return cloud_oauth.authorize_url(self.cloud.service, oauth, redirect_uri, state, challenge)

    def finish_browser(self, state: str, code: str = "", error: str = "") -> str:
        """The service's page came back (the callback): the code traded and
        the connection saved, replacing any. "" once connected, else why
        not, which ``status``'s ``error`` says too."""
        with self._lock:
            started = self._browser.pop(state, None) if state else None
        denied = f"{self.name} access was not allowed."
        if started is None or started["expires_at"] <= time.time():
            problem = "This sign-in expired or was already used: connect again."
        elif error in ("access_denied", "consent_required"):
            problem = denied
        elif error or not code:
            problem = f"{self.cloud.service.name} did not sign in ({error or 'no code'}): connect again."
        else:
            try:
                answer = cloud_oauth.exchange_code(self.cloud.service, started["oauth"], code, started["verifier"],
                                                   started["redirect_uri"])
            except (CloudError, CloudConfigError) as e:
                answer = {"state": "failed", "error": str(e)}
            if answer["state"] == "done":
                problem = self._connect(answer, started["folder"], started["oauth"])
            else:
                problem = answer.get("error") or denied
        with self._lock:
            self._last_error = problem
        return problem

    def poll(self) -> dict:
        """Ask the service once whether the code was entered, unless the
        interval it gave has not passed or another request is asking (then
        nothing is asked). No answer, or a 5xx, waits for the next
        interval. On a yes the connection is saved, replacing any; on a no,
        or a code that expired, the flow ends with the reason in
        ``status``'s ``error``. The new ``status``."""
        with self._lock:
            pending = self._pending
            due = pending is not None and time.time() >= pending["next_poll_at"]
            if due:
                pending["next_poll_at"] = float("inf")  # this request asks; another waits for its answer
        if not due:
            return self.status()
        try:
            answer = cloud_oauth.poll_device_flow(self.cloud.service, pending["oauth"], pending["device_code"])
        except CloudUnreachable:
            answer = {"state": "pending"}
        except (CloudError, CloudConfigError) as e:
            answer = {"state": "failed", "error": str(e)}
        state = answer["state"]
        if state in ("pending", "slow_down"):
            with self._lock:
                pending["interval"] += 5 if state == "slow_down" else 0
                pending["next_poll_at"] = time.time() + pending["interval"]
            return self.status()
        error = self._connect(answer, pending["folder"], pending["oauth"]) if state == "done" else {
            "denied": f"{self.name} access was not allowed.",
            "expired": "The code expired before it was entered: connect again."}.get(state) or answer.get("error", "")
        with self._lock:
            if self._pending is pending:  # not replaced by a new connection meanwhile
                self._end(error)
        return self.status()

    def _connect(self, answer: dict, folder: str, oauth: cloud_oauth.OAuthClient) -> str:
        """Save the connection the service just granted through ``oauth``,
        replacing (and revoking) any; "" or why it could not be saved."""
        token, access = answer["refresh_token"], answer["access_token"]
        try:
            who = self.cloud.make(lambda _renew=False: access, folder).about()
        except CloudConfigError as e:
            self._revoke(token)
            return f"{self.cloud.service.name} said yes, but the access could not be used: {e}"
        except CloudError as e:  # the files work without the address
            log.warning(f"[{self.key}] the connected account could not be read: {e}")
            who = {"email": "", "name": ""}
        with self._lock:
            old = self._saved().get("refresh_token")
            self._save({"refresh_token": token, "email": who["email"], "name": who["name"], "folder": folder,
                        "connected_at": _iso(time.time()), "client_id": oauth.id})
            self._held = (_digest(token), access, time.monotonic() + answer["expires_in"])
        if old and old != token:
            self._revoke(old)
        log.info(f"[{self.key}] connected {who['email'] or 'an account'}")
        return ""

    def _revoke(self, token: str) -> None:
        try:
            cloud_oauth.revoke(self.cloud.service, token)
        except (CloudError, CloudConfigError) as e:  # it stays in the person's list at the service
            log.warning(f"[{self.key}] the old access could not be revoked: {e}")

    def disconnect(self) -> dict:
        """Forget the connection, revoking it at the service first where it
        can (a failure there is logged: the person can remove it from their
        account). The copies stay."""
        with self._lock:
            token = self._saved().get("refresh_token")
            _set_raw(self.cloud.settings_key, "")
            self._held = None
        if token:
            self._revoke(token)
        return self.status()

    def set_folder(self, folder: str) -> dict:
        """The folder new copies go to. ValueError when it cannot be, or when
        nothing is connected."""
        folder = self.check_folder(folder)
        with self._lock:
            conn = self._saved()
            if not conn.get("refresh_token"):
                raise ValueError(f"{self.name} is not connected.")
            self._save({**conn, "folder": folder})
        return self.status()

    def test(self) -> dict:
        """The pane's Test: what the folder holds and the account's room,
        tried once: ``{ok: True, message}`` or ``{ok: False, message:
        <why>}``."""
        try:
            store = self.client(attempts=1)
            count, more = store.check()
        except (CloudConfigError, CloudError) as e:
            return {"ok": False, "message": str(e)}
        try:
            who = store.about()
        except CloudError:
            who = {"email": "", "used": 0, "limit": 0}
        held = f"{count} or more items" if more else f"{count} item{'' if count == 1 else 's'}"
        room = f", {who['used'] / 1e9:.1f} of {who['limit'] / 1e9:.0f} GB used" if who["limit"] else ""
        return {"ok": True, "message": f"{who['email'] or self.name}: {held} in {cloud_http.label(store.folder)}{room}"}


ACCOUNTS = {cloud.key: CloudAccount(cloud) for cloud in CLOUDS}


def account(key: str) -> CloudAccount:
    """The cloud named ``key``; ValueError for a name none has."""
    found = ACCOUNTS.get(key) if isinstance(key, str) else None
    if found is None:
        raise ValueError(f"no cloud is named {key!r}")
    return found

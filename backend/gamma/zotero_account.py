"""The Zotero account a Gamma account connected (docs/dev/zotero_sync.md):
its API key, which every Zotero link of the account syncs with.

The connection is the account-wide pref ``zotero-account``: ``{"user_id",
"username", "key", "hint", "access", "via", "connected_at"}``, ``key``
sealed with the data directory's Fernet key (publisher_sessions.seal, as
the mirrors' tokens are), so a copy of users.db alone does not hold it. The
pref is reserved: /api/prefs refuses it (routers/prefs.py), the synced
profile never carries it, and ``status`` shows only a hint. ``via`` says
how the key came: "oauth" keys Gamma had Zotero make, so a disconnect (or
a newer connection) revokes them at Zotero; a pasted key ("key") is the
person's own and is only forgotten. ``access`` is what the key could do
when it connected; a sync asks Zotero again (``ZoteroClient.libraries``).

Refusals are ``AccountError`` (a ValueError with an HTTP ``status``, a
``kind`` and a sentence), which routers/zotero.py answers and a background
sync records.
These functions check nothing about the account: the routes refuse guests
(``auth.require_personal_user_id``) before calling.
"""

import json
import re

from cryptography.fernet import InvalidToken

from .db import connect_users_db, get_pref, page_now, update_pref
from .publisher_sessions import seal, unseal
from .zotero_web import ZoteroAuthError, ZoteroClient, ZoteroError, my_library_access

PREF_KEY = "zotero-account"
VIA = ("oauth", "key")
# What can travel in a header: printable ASCII, no spaces (Zotero's own
# keys are 24 letters and digits).
_KEY_RE = re.compile(r"^[\x21-\x7e]{1,128}$")
NOT_CONNECTED = "Connect your Zotero account first, in Settings → Account & sync"
UNREADABLE = ("Gamma can no longer read your stored Zotero key (this server's encryption key changed): "
              "reconnect your Zotero account in Settings → Account & sync")
NOT_A_KEY = "That is not a Zotero API key: copy the whole key from zotero.org/settings/keys"
REFUSED = "Zotero does not accept this key: check that it was copied whole and has not been deleted"
NO_LIBRARY = ("This key cannot read your Zotero library: at zotero.org/settings/keys, edit it and tick "
              "“Allow library access”")
# The pref's row: account-wide prefs live in users.db under workspace '' (db.pref_scope).
_ROW = "SELECT value FROM user_prefs WHERE user_id = ? AND workspace_id = '' AND key = ?"


class AccountError(ValueError):
    """The connection cannot be made or used; the text is a sentence for
    the person. ``status`` and ``kind`` (the words a link's status uses for
    its error, docs/dev/zotero_sync.md): 400 "auth" (Zotero refuses the
    key), 409 "not_connected" (none is connected) or "auth" (the stored
    key can no longer be read: reconnect), 502 "offline" (Zotero could not
    be asked)."""

    def __init__(self, message: str, status: int, kind: str = "auth"):
        super().__init__(message)
        self.status, self.kind = status, kind


def _stored(user_id: str) -> dict | None:
    value, _ = get_pref(user_id, PREF_KEY)
    return value if isinstance(value, dict) and value.get("key") else None


def _groups(access: dict) -> str:
    """The key's group access in one word: "none", "read" or "write" for
    all groups, "some" when it names groups one by one."""
    groups = access.get("groups") or {}
    every = groups.get("all")
    if isinstance(every, dict):
        if not every.get("library"):
            return "none"
        return "write" if every.get("write") else "read"
    return "some" if any(isinstance(g, dict) and g.get("library") for g in groups.values()) else "none"


def status(user_id: str) -> dict:
    """The connection as Settings shows it, never the key:
    ``{"connected": False}`` or the account, the key's hint, how it came
    and what it could do in My Library when it connected (``access``:
    library, notes, write, files as booleans, read as
    ``zotero_web.my_library_access`` and ``ZoteroClient.libraries`` read
    them; ``groups`` as ``_groups`` words it)."""
    stored = _stored(user_id)
    if not stored:
        return {"connected": False}
    access = stored.get("access") if isinstance(stored.get("access"), dict) else {}
    mine = my_library_access(access)
    return {"connected": True, "user_id": stored.get("user_id"), "username": stored.get("username", ""),
            "hint": stored.get("hint", ""), "via": stored.get("via", "key"),
            "connected_at": stored.get("connected_at", ""),
            "access": {"library": mine["read"], **{name: mine[name] for name in ("notes", "write", "files")},
                       "groups": _groups(access)}}


def _unsealed(stored: dict) -> str | None:
    try:
        return unseal(stored["key"])
    except (InvalidToken, ValueError):
        return None


def _revoke_made_key(stored, unless: str = "") -> None:
    """Revoke a replaced or forgotten connection's key at Zotero when Gamma
    had it made (OAuth) and it is not ``unless``."""
    if not isinstance(stored, dict) or stored.get("via") != "oauth" or not stored.get("key"):
        return
    key = _unsealed(stored)
    if key and key != unless:
        ZoteroClient(key).revoke()


def connect(user_id: str, api_key: str, *, via: str = "key") -> dict:
    """Check ``api_key`` with Zotero and keep it as the account's
    connection (replacing any before); the new ``status``. AccountError 400
    when it is no key, Zotero does not know it or it cannot read the
    person's own library, 502 when Zotero cannot be asked."""
    if via not in VIA:
        raise ValueError(f"unknown Zotero connection kind: {via!r}")
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    if not _KEY_RE.match(api_key):
        raise AccountError(NOT_A_KEY, 400)
    try:
        info = ZoteroClient(api_key).key_info()
    except ZoteroAuthError:
        raise AccountError(REFUSED, 400) from None
    except ZoteroError as e:
        raise AccountError(str(e), 502, "offline") from None
    access = info.get("access") if isinstance(info.get("access"), dict) else {}
    if not my_library_access(access)["read"]:
        if via == "oauth":  # made for this connection and useless to it: not left behind at Zotero
            ZoteroClient(api_key).revoke()
        raise AccountError(NO_LIBRARY, 400)
    value = {"user_id": info["userID"], "username": str(info.get("username") or ""), "key": seal(api_key),
             "hint": f"…{api_key[-4:]}" if len(api_key) >= 8 else "…", "access": access, "via": via,
             "connected_at": page_now()}
    replaced = []

    def change(stored):
        replaced.append(stored)
        return value

    update_pref(user_id, PREF_KEY, change)
    _revoke_made_key(replaced[-1], unless=api_key)
    return status(user_id)


def disconnect(user_id: str, *, revoke: bool = True) -> None:
    """Forget the account's connection (its links pause, asking for one):
    its row goes. With ``revoke`` also delete its key at Zotero when Gamma
    had it made (a pasted key stays valid there)."""
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the row read and removed in one go, as update_pref does
        row = conn.execute(_ROW, (user_id, PREF_KEY)).fetchone()
        conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND workspace_id = '' AND key = ?",
                     (user_id, PREF_KEY))
        conn.commit()
    if row and revoke:
        try:
            stored = json.loads(row[0])
        except ValueError:
            return
        _revoke_made_key(stored)


def api_key(user_id: str) -> str | None:
    """The connected key; None when there is none; AccountError 409 when it
    can no longer be read (``UNREADABLE``)."""
    stored = _stored(user_id)
    if not stored:
        return None
    key = _unsealed(stored)
    if not key:
        raise AccountError(UNREADABLE, 409)
    return key


def client_for(user_id: str, *, heartbeat=None) -> ZoteroClient:
    """A client with the account's key; AccountError 409 when none is
    connected (``NOT_CONNECTED``) or it cannot be read."""
    key = api_key(user_id)
    if not key:
        raise AccountError(NOT_CONNECTED, 409, "not_connected")
    return ZoteroClient(key, heartbeat=heartbeat)

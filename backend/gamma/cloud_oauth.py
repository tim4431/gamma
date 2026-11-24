"""OAuth 2.0 for the cloud backup stores (docs/dev/debugging.md "Cloud
destinations"): how a server signs in to Google for Drive and to Microsoft
for OneDrive, with nothing set up first. Each provider is a ``Service``
(gamma/gdrive.py ``GOOGLE``, gamma/onedrive.py ``MICROSOFT``): its
endpoints, the scopes asked for and the one a grant must hold, and what
differs between the two (Google's installed clients send their secret,
which Google does not treat as secret; a Microsoft public client sends
none; Microsoft has no endpoint to revoke a token). gamma/cloud_account.py
keeps the connection and calls these.

Two ways to sign in, both standard:

- **The device flow** (RFC 8628): ``start_device_flow`` asks for a code the
  person enters at the service's address on any device, and
  ``poll_device_flow`` asks once whether they did. Works for a server at
  any address, since nothing comes back to it.
- **The authorization page** with PKCE (RFC 7636): ``authorize_url`` for a
  browser on the server's own computer, which the service sends back to a
  loopback address with a code that ``exchange_code`` trades.

Either ends in a refresh token, which ``refresh`` trades for an access token
(an hour's): a service that rotates refresh tokens (Microsoft) answers a
new one with it, which the caller keeps. ``revoke`` ends a connection where
the service can. Every call is a form POST through gamma/cloud_http.py.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from . import cloud_http
from .cloud_http import CloudConfigError, CloudError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass(frozen=True)
class OAuthClient:
    """An OAuth client a connection is made through and its tokens renewed
    by; ``secret`` "" for a public client."""
    id: str
    secret: str = field(default="", repr=False)


@dataclass(frozen=True)
class Service:
    """An OAuth provider. The URLs are functions, read at each call, so a
    test's stand-in address takes. ``required``: the scope a grant must
    hold (the person may untick it on Google's page); ``client_hint`` what
    a refused client must be, for the message."""
    name: str
    device_url: Callable[[], str]
    token_url: Callable[[], str]
    authorize_url: Callable[[], str]
    scopes: tuple[str, ...]
    required: str
    verification_url: str
    client_hint: str
    send_secret: bool = True
    revoke_url: Callable[[], str] | None = None
    authorize_params: tuple[tuple[str, str], ...] = ()


def _with_secret(service: Service, oauth: OAuthClient, fields: dict) -> dict:
    return {**fields, "client_secret": oauth.secret} if service.send_secret and oauth.secret else fields


def _granted(service: Service, scope: str) -> bool:
    """Whether a grant's ``scope`` holds the one the store needs, by its
    full name or its short one (Microsoft answers either)."""
    want = service.required.lower()
    return any(s == want or s.endswith("/" + want) for s in scope.lower().split())


def _refused_client(service: Service, reason: str) -> CloudConfigError:
    return CloudConfigError(f"{service.name} refused the OAuth client ({reason}): {service.client_hint}")


def _tokens(service: Service, resp: cloud_http.Response) -> dict | None:
    """A token answer as ``{state: "done", refresh_token, access_token,
    expires_in, scope}``, ``{state: "denied"}`` for a grant without the
    scope needed, None for a refusal. CloudError for a grant with no
    refresh token."""
    data = resp.json()
    if resp.status != 200 or not data.get("access_token"):
        return None
    scope = str(data.get("scope", ""))
    if not _granted(service, scope):
        return {"state": "denied"}
    if not data.get("refresh_token"):
        raise CloudError(f"{service.name} gave no refresh token")
    return {"state": "done", "refresh_token": str(data["refresh_token"]), "access_token": str(data["access_token"]),
            "expires_in": int(data.get("expires_in") or 3600), "scope": scope}


def start_device_flow(service: Service, oauth: OAuthClient) -> dict:
    """Ask for a device code: ``{device_code, user_code, verification_url,
    expires_in, interval}``. CloudConfigError for a client the service
    refuses, CloudError when it gives no code."""
    resp = cloud_http.form(service.device_url(), {"client_id": oauth.id, "scope": " ".join(service.scopes)},
                           attempts=cloud_http.INTERACTIVE_ATTEMPTS)
    data = resp.json()
    if resp.status != 200 or not data.get("device_code") or not data.get("user_code"):
        reason, message = cloud_http.error(resp)
        if reason in ("invalid_client", "unauthorized_client"):
            raise _refused_client(service, reason)
        raise CloudError(f"{service.name} did not give a sign-in code: {message or reason or resp.status}")
    return {"device_code": str(data["device_code"]), "user_code": str(data["user_code"]),
            "verification_url": str(data.get("verification_url") or data.get("verification_uri")
                                    or service.verification_url),
            "expires_in": int(data.get("expires_in") or 900), "interval": max(int(data.get("interval") or 5), 1)}


def poll_device_flow(service: Service, oauth: OAuthClient, device_code: str) -> dict:
    """Ask once whether the person entered the code: ``{state: "pending"}``,
    ``{state: "slow_down"}``, ``{state: "denied"}``, ``{state: "expired"}``,
    or ``{state: "done", refresh_token, access_token, expires_in, scope}``.
    Tried once: a 5xx is ``pending`` and a 429 ``slow_down``, for the next
    interval; CloudUnreachable when no answer came. A grant without the
    scope needed is ``denied``; CloudError for anything else."""
    resp = cloud_http.form(service.token_url(), _with_secret(service, oauth, {
        "client_id": oauth.id, "device_code": device_code, "grant_type": DEVICE_GRANT}),
        attempts=1)  # the pane asks again at the next interval
    done = _tokens(service, resp)
    if done:
        return done
    reason, message = cloud_http.error(resp)
    if reason == "slow_down" or resp.status == 429:
        return {"state": "slow_down"}
    if reason == "authorization_pending" or cloud_http.retried(resp):
        return {"state": "pending"}
    if reason in ("access_denied", "authorization_declined"):
        return {"state": "denied"}
    if reason in ("expired_token", "invalid_grant", "bad_verification_code"):
        return {"state": "expired"}
    if reason in ("invalid_client", "unauthorized_client"):
        raise _refused_client(service, reason)
    raise CloudError(f"{service.name}'s answer to the sign-in: {message or reason or resp.status}")


def authorize_url(service: Service, oauth: OAuthClient, redirect_uri: str, state: str, challenge: str) -> str:
    """The service's authorization page for a browser on the server's own
    computer, which sends it to ``redirect_uri`` (a loopback address) with
    ``state`` and a code bound to ``challenge`` (PKCE, S256)."""
    params = {"client_id": oauth.id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(service.scopes), "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256", **dict(service.authorize_params)}
    return f"{service.authorize_url()}?{urllib.parse.urlencode(params)}"


def exchange_code(service: Service, oauth: OAuthClient, code: str, verifier: str, redirect_uri: str) -> dict:
    """The authorization page's code traded for the tokens: as
    ``poll_device_flow``'s ``done`` or ``denied``. CloudError when the
    service refuses the code."""
    resp = cloud_http.form(service.token_url(), _with_secret(service, oauth, {
        "client_id": oauth.id, "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"}), attempts=cloud_http.INTERACTIVE_ATTEMPTS)
    done = _tokens(service, resp)
    if done:
        return done
    reason, message = cloud_http.error(resp)
    if reason in ("invalid_client", "unauthorized_client"):
        raise _refused_client(service, reason)
    raise CloudError(f"{service.name} did not take the sign-in: {message or reason or resp.status}")


def refresh(service: Service, oauth: OAuthClient, refresh_token: str, *, again: str = "",
            attempts: int = cloud_http.ATTEMPTS) -> tuple[str, int, str]:
    """A new access token for ``refresh_token``: ``(access token, lifetime
    in seconds, the refresh token to keep)``, the last a new one where the
    service rotates them (Microsoft), else the one given. CloudConfigError,
    ending in ``again`` (what to do), when the service no longer honours
    it. ``attempts``: fewer for a caller a person waits on (the Gemini
    sign-in renews its token in front of a chat request)."""
    resp = cloud_http.form(service.token_url(), _with_secret(service, oauth, {
        "client_id": oauth.id, "refresh_token": refresh_token, "grant_type": "refresh_token"}), attempts=attempts)
    data = resp.json()
    if resp.status == 200 and data.get("access_token"):
        return (str(data["access_token"]), int(data.get("expires_in") or 3600),
                str(data.get("refresh_token") or refresh_token))
    reason, message = cloud_http.error(resp)
    if reason in ("invalid_grant", "interaction_required"):
        raise CloudConfigError(f"{service.name} no longer honours this connection (revoked, or unused for months)"
                               + (f": {again}" if again else ""))
    if reason in ("invalid_client", "unauthorized_client"):
        raise CloudConfigError(f"{service.name} refused the OAuth client ({reason})" + (f": {again}" if again else ""))
    raise CloudError(f"{service.name} did not renew the access: {message or reason or resp.status}")


def revoke(service: Service, token: str) -> bool:
    """Revoke a refresh token (and the access it gave) at the service; one
    already revoked is no error. False where the service has no way to
    (Microsoft: the person removes the app from their account)."""
    if service.revoke_url is None:
        return False
    resp = cloud_http.form(service.revoke_url(), {"token": token}, attempts=cloud_http.INTERACTIVE_ATTEMPTS)
    if resp.status != 200 and cloud_http.error(resp)[0] != "invalid_token":
        raise CloudError(f"{service.name} did not revoke the access: {cloud_http.error(resp)[1] or resp.status}")
    return True

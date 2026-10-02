"""Cloudflare Turnstile on register and reset. Off (always passes) until an
admin sets the secret on the Admin page, so a local run and the tests need
no widget."""

import json
import urllib.parse
import urllib.request

from . import settings
from .log import log

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify(token: str | None, ip: str) -> bool:
    secret = settings.turnstile_secret()
    if not secret:
        return True
    if not token:
        return False
    data = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": ip}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(VERIFY_URL, data=data), timeout=10) as resp:
            return bool(json.load(resp).get("success"))
    except (OSError, ValueError) as e:
        log.warning("turnstile verify failed: %s", e)
        return False

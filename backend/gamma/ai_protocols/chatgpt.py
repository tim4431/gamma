"""ChatGPT subscription sign-in: the Codex Responses backend, reached with
the OAuth tokens of Codex CLI's flow (gamma/chatgpt_oauth.py) — usage is
billed to the subscription. The backend speaks the Responses API but lists
models, checks quota and gates on the client version the way Codex CLI's
own client does; those contracts are provider-specific and may need
maintenance when upstream changes."""

import json
import re
import threading
import time
import uuid
from urllib.request import Request as URLRequest, urlopen

from .. import chatgpt_oauth
from ..logbuf import log
from .base import listed_window
from .responses import ResponsesWire, responses_body

# GET {base}/models gates its answer on the caller's version, so the listing
# claims the newest Codex CLI release (npm's `latest` tag), looked up live and
# cached. The floor is only for when npm can't be reached.
CODEX_VERSION_URL = "https://registry.npmjs.org/@openai/codex/latest"
CODEX_VERSION_FLOOR = "0.156.1"
CODEX_VERSION_TTL = 6 * 3600       # a good answer
CODEX_VERSION_RETRY = 600          # after a failed lookup
LOOKUP_TIMEOUT = 5
_codex_version = {"value": "", "until": 0.0}
_codex_version_lock = threading.Lock()


def codex_client_version() -> str:
    """The newest Codex CLI version, cached; the last good one (else the
    floor) while npm is unreachable."""
    with _codex_version_lock:
        now = time.time()
        if now < _codex_version["until"]:
            return _codex_version["value"] or CODEX_VERSION_FLOOR
        try:
            with urlopen(URLRequest(CODEX_VERSION_URL, headers={"Accept": "application/json"}),
                         timeout=LOOKUP_TIMEOUT) as resp:
                version = str(json.loads(resp.read()).get("version") or "").strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError(f"unexpected version {version!r}")
            _codex_version.update(value=version, until=now + CODEX_VERSION_TTL)
        except Exception as e:
            log.warning(f"[ai] codex version lookup failed, using "
                        f"{_codex_version['value'] or CODEX_VERSION_FLOOR}: {e}")
            _codex_version["until"] = now + CODEX_VERSION_RETRY
        return _codex_version["value"] or CODEX_VERSION_FLOOR


def _usage_window(raw, name: str = "") -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        used = max(0.0, min(100.0, float(raw.get("used_percent", 0))))
    except (TypeError, ValueError):
        return None
    try:
        seconds = max(0, int(raw.get("limit_window_seconds") or 0))
    except (TypeError, ValueError):
        seconds = 0
    try:
        reset_at = int(raw.get("reset_at") or 0)
    except (TypeError, ValueError):
        reset_at = 0
    if not name:
        if 4 * 3600 <= seconds <= 6 * 3600:
            name = "5-hour"
        elif 6 * 86400 <= seconds <= 8 * 86400:
            name = "Weekly"
        elif seconds:
            name = f"{max(1, round(seconds / 3600))}-hour"
        else:
            name = "Usage"
    return {"name": name, "used_percent": used, "remaining_percent": max(0.0, 100.0 - used),
            "window_seconds": seconds, "reset_at": reset_at}


class ChatGPT(ResponsesWire):
    id = "chatgpt"
    label = "ChatGPT (subscription sign-in)"
    auth = "oauth"
    oauth = chatgpt_oauth
    native_pdf = False  # the Codex backend refuses input_file parts; the PDF goes as text
    has_account_usage = True

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None):
        body = {**responses_body(messages, model, pdf_b64s, images, tools, effort),
                "instructions": system or "You are a helpful research assistant.",
                "include": []}
        return URLRequest(f"{conf['base_url']}/responses", data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "chatgpt-account-id": conf.get("account_id", ""),
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "session_id": str(uuid.uuid4()),
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        })

    def models_request(self, conf):
        # Codex CLI's own listing call.
        return URLRequest(f"{conf['base_url']}/models?client_version={codex_client_version()}", headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "chatgpt-account-id": conf.get("account_id", ""),
            "originator": "codex_cli_rs",
        })

    def models(self, data, conf):
        listed, hidden = {}, {}
        for m in data.get("models") or []:
            if not isinstance(m, dict):
                continue
            slug = str(m.get("slug") or "").strip()
            visibility = m.get("visibility") or "list"
            if not slug or visibility == "none":  # "none" = not usable by this account
                continue
            # "hide" marks picker-hidden but usable slugs — offered after the
            # listed ones rather than dropped.
            (hidden if visibility == "hide" else listed).setdefault(slug, listed_window(m))
        found = {**listed, **{k: v for k, v in hidden.items() if k not in listed}}
        return [{"id": slug, "context_window": window} for slug, window in found.items()]

    def account_usage_request(self, conf):
        # Codex's account client's .../backend-api/wham/usage, sibling of the
        # .../codex model endpoint. Always the administrator-controlled
        # protocol endpoint, never a saved entry value: OAuth entries cannot
        # redirect their bearer token.
        base = self.base_url.rstrip("/")
        account_base = base[:-len("/codex")] if base.endswith("/codex") else base
        headers = {"Authorization": f"Bearer {conf['api_key']}", "Accept": "application/json",
                   "User-Agent": "codex-cli"}
        if conf.get("account_id"):
            headers["ChatGPT-Account-Id"] = conf["account_id"]
        return URLRequest(f"{account_base}/wham/usage", headers=headers, method="GET")

    def account_usage(self, data):
        rate = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
        windows = [w for w in (_usage_window(rate.get("primary_window")),
                               _usage_window(rate.get("secondary_window"))) if w]
        for extra in data.get("additional_rate_limits") or []:
            if not isinstance(extra, dict):
                continue
            extra_rate = extra.get("rate_limit") if isinstance(extra.get("rate_limit"), dict) else {}
            window = _usage_window(extra_rate.get("primary_window"),
                                   str(extra.get("limit_name") or "Additional limit"))
            if window:
                windows.append(window)
        return {"plan_type": str(data.get("plan_type") or ""), "windows": windows,
                "credits": data.get("credits") if isinstance(data.get("credits"), dict) else None}

    def ping_request(self, conf):
        # The quota endpoint answers 401 on a dead sign-in, for free.
        return self.account_usage_request(conf)

    def catalog_hints(self, conf):
        return ["openai"]  # models.dev lists these models under OpenAI

"""Subscription quota readers: what is left of a plan, as windows Settings
draws as bars. A connection has one when its protocol reads its own (the
ChatGPT sign-in, ``Protocol.has_account_usage``) or when its service preset
names one (``usage``). Every reader answers the ChatGPT reader's shape:
``account_usage_request(conf)`` builds the request, ``account_usage(data)``
reads ``{plan_type, windows: [{name, used_percent, remaining_percent,
window_seconds, reset_at (epoch s)}], credits}`` out of the answer, and
``refusal(status)`` words an HTTP status that means the key does not cover
the plan ("" for the usual handling)."""

from datetime import datetime
from urllib.request import Request as URLRequest

from .base import bearer_json_request

OPENCODE_GO_USAGE_URL = "https://opencode.ai/inference/go/v1/usage"


def _epoch(stamp) -> int:
    """An ISO 8601 time as epoch seconds, 0 when it is none."""
    try:
        return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


class OpenCodeGo:
    """OpenCode Go's limits: ``GET /inference/go/v1/usage`` with the Go key
    answers one meter per limit, ``{"usage": {"rolling" | "weekly" |
    "monthly": {"status", "percent", "resetsAt"}}}`` — the 5-hour window,
    the week from Monday 00:00 UTC and the paid month. A key Go does not
    cover answers 403 (an ``EntitlementError``)."""

    # Each meter's name as the bars show it, and its length (0 = a month's).
    WINDOWS = (("rolling", "5-hour", 5 * 3600), ("weekly", "Weekly", 7 * 86400), ("monthly", "Monthly", 0))

    def account_usage_request(self, conf) -> URLRequest:
        # Always OpenCode's own endpoint: only an entry of the Go preset has
        # this reader, so the key goes nowhere else.
        return bearer_json_request(OPENCODE_GO_USAGE_URL, conf["api_key"])

    def account_usage(self, data) -> dict:
        meters = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        windows = []
        for key, name, seconds in self.WINDOWS:
            meter = meters.get(key)
            if not isinstance(meter, dict):
                continue
            try:
                used = max(0.0, min(100.0, float(meter.get("percent"))))
            except (TypeError, ValueError):
                continue
            windows.append({"name": name, "used_percent": used, "remaining_percent": 100.0 - used,
                            "window_seconds": seconds, "reset_at": _epoch(meter.get("resetsAt"))})
        return {"plan_type": "", "windows": windows, "credits": None}

    def refusal(self, status: int) -> str:
        return "This key doesn't cover OpenCode Go." if status == 403 else ""


READERS = {"opencode-go": OpenCodeGo()}

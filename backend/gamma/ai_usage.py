"""Token usage bookkeeping: one ``ai_usage`` row in users.db per AI call an
account makes (chat turn, translation batch, metadata extraction, …), and
the summary Settings → AI → Usage shows. Counts come from the providers'
own reports (``ai_client.normalize_usage``); a provider that reports
nothing leaves no row.

Each row also says how the call was paid for (``billing``: "api" per token,
"subscription", "shared" through the server's entry, "local", or "" for a
row from before it was kept) and, for a call paid per token, what it cost
(``cost``, USD) at the public price of the day it was made
(``ai_catalog.price``, ``cost_of``); NULL when no price names the model or
no money is spent per token. Subscriptions and shared use show tokens and
quota, never dollars."""

import sqlite3
from datetime import datetime, timedelta, timezone

from .db import connect_users_db, page_now
from .logbuf import log

KINDS = ("chat", "translate", "metadata", "cite", "ink", "test")
BILLINGS = ("api", "subscription", "shared", "local")
# The provider ids of the server's shared entries (ai_settings.SERVER_ID_PREFIX
# is this constant): their rows are what the shared allowance meters.
SHARED_PREFIX = "server:"
ALLOWANCE_HOURS = 24  # the allowance's rolling window
_FIELDS = ("input", "output", "cache_read", "cache_write")
# Rows older than this are dropped on the next write. Keep enough history
# for the 365-day activity calendar as well as the rolling summaries.
KEEP_DAYS = 400
# The summary's periods (GET /api/ai/usage?period=): days back from now,
# None for every row kept.
PERIODS = {"week": 7, "month": 30, "year": 365, "all": None}


def record(user_id: str, kind: str, provider_id: str, provider_name: str,
           model: str, usage: dict | None, billing: str = "", cost: float | None = None) -> None:
    """Store one call's token counts, how it was paid for and its cost (None
    = unknown, or nothing spent per token). Never raises — usage is a
    courtesy, not something a failing insert should turn into a failed
    chat."""
    if not usage or not user_id or kind not in KINDS:
        return
    counts = [max(0, int(usage.get(f) or 0)) for f in _FIELDS]
    if not any(counts):
        return
    try:
        with connect_users_db() as conn:
            conn.execute(
                "INSERT INTO ai_usage (user_id, at, kind, provider_id, provider_name, model, "
                "input, output, cache_read, cache_write, billing, cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, page_now(), kind, str(provider_id or "")[:64],
                 str(provider_name or "")[:80], str(model or "")[:120], *counts,
                 billing if billing in BILLINGS else "", cost if billing == "api" else None))
            cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute("DELETE FROM ai_usage WHERE user_id = ? AND at < ?", (user_id, cutoff))
    except sqlite3.Error as e:
        log.warning(f"[ai_usage] could not record usage: {e}")


def _tariff_for(price: dict, prompt: int) -> dict:
    """The tariff a call with a ``prompt`` of that many tokens pays: the
    price's largest context tier the whole prompt is over, else its base."""
    chosen = price
    for tier in sorted(price.get("tiers") or [], key=lambda t: t.get("size") or 0):
        if prompt > (tier.get("size") or 0):
            chosen = tier
    return chosen


def cost_of(usage: dict | None, price: dict | None, speed: str = "") -> float | None:
    """What one call cost in USD, from its token counts and the model's price
    (``ai_catalog.price``, USD per 1M tokens): the uncached input (``input``
    holds the cached parts, so input − cache_read − cache_write, at least 0)
    at the input price, the cache reads and writes at theirs (the input
    price where none is named: an upper bound), the output at the output
    price — at the tariff of the context tier the whole prompt reaches. A
    call served at a non-default ``speed`` (flex, fast) costs that tier's
    factor (``price["speeds"]``), unknown where the price names none for
    it. None without a price or counts."""
    if not price or not usage:
        return None
    counts = {f: max(0, int(usage.get(f) or 0)) for f in _FIELDS}
    tariff = _tariff_for(price, counts["input"])
    factor = 1.0
    if speed:
        factor = (price.get("speeds") or {}).get(speed)
        if factor is None:
            return None
    rate_in = tariff["input"]
    uncached = max(0, counts["input"] - counts["cache_read"] - counts["cache_write"])
    total = (uncached * rate_in + counts["cache_read"] * tariff.get("cache_read", rate_in)
             + counts["cache_write"] * tariff.get("cache_write", rate_in) + counts["output"] * tariff["output"])
    return round(total * factor / 1e6, 8)


def recorder(kind: str, entry: dict, runtime: dict, speed: str = ""):
    """An ``on_usage`` callback for ``ai_client`` bound to one call site:
    the runtime (``ai_settings.ai_runtime``) names the account and the
    connection, the model registry entry the provider and model. ``speed``
    is the tier the call asked for; a report naming the one it was served
    at (``speed`` in the usage) wins. Each report it hears gets the call's
    ``billing`` and ``cost`` written onto it, so whatever passes the report
    on (the chat's usage lines, a sum) carries them."""
    if not isinstance(entry, dict):  # a bare model id (tests stub the resolver so)
        entry = {"model": str(entry or "")}
    user_id = runtime.get("user") or ""
    conf = (runtime.get("providers") or {}).get(entry.get("provider"), {})
    billing = conf.get("billing") or ""

    def on_usage(usage):
        if not isinstance(usage, dict):
            return
        cost = None
        if billing == "api" and conf:
            from . import ai_catalog  # the catalog's fetches stay out of this module's imports
            served = usage.get("speed")
            price = ai_catalog.price(conf, entry.get("model", ""), entry.get("provider", ""),
                                     wait=ai_catalog.RECORD_WAIT)
            cost = cost_of(usage, price, speed if served is None else served)
        usage["billing"] = billing
        usage["cost"] = cost
        record(user_id, kind, entry.get("provider", ""), conf.get("name", ""),
               entry.get("model", ""), usage, billing, cost)
    return on_usage


# The rows the shared allowance counts: shared entries, the last 24 hours
# (bound to ``_metered_args()``).
_METERED = "at >= ? AND substr(provider_id, 1, ?) = ?"


def _metered_args() -> tuple:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALLOWANCE_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    return cutoff, len(SHARED_PREFIX), SHARED_PREFIX


def shared_used(user_id: str) -> int:
    """Tokens (input + output) the account spent through the server's shared
    entries in the last 24 hours — what the shared AI allowance counts
    (docs/dev/guests.md). Own entries never count."""
    if not user_id:
        return 0
    try:
        with connect_users_db() as conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(input + output), 0) FROM ai_usage WHERE user_id = ? AND {_METERED}",
                (user_id, *_metered_args())).fetchone()
    except sqlite3.Error as e:
        log.warning(f"[ai_usage] could not read shared usage: {e}")
        return 0
    return int(row[0] or 0)


def _empty() -> dict:
    return {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _counts() -> dict:
    """A counts object: the calls and tokens, the known cost, how many api
    calls had a price and how many had none, and the same split by billing."""
    return {**_empty(), "cost": 0.0, "priced_calls": 0, "unpriced_calls": 0, "billing": {}}


def _add(counts: dict, row) -> None:
    """Count one row into a counts object (``_counts``)."""
    cost = row["cost"]
    counts["calls"] += 1
    for f in _FIELDS:
        counts[f] += row[f]
    if row["billing"] == "api":
        if cost is None:
            counts["unpriced_calls"] += 1
        else:
            counts["priced_calls"] += 1
    split = counts["billing"].setdefault(row["billing"], {**_empty(), "cost": 0.0})
    split["calls"] += 1
    for f in _FIELDS:
        split[f] += row[f]
    if cost is not None:
        counts["cost"] += cost
        split["cost"] += cost


def _rounded(counts: dict) -> dict:
    counts["cost"] = round(counts["cost"], 6)
    for split in counts["billing"].values():
        split["cost"] = round(split["cost"], 6)
    return counts


def _totals(rows) -> dict:
    total = _counts()
    for row in rows:
        _add(total, row)
    return _rounded(total)


def summary(user_id: str, period: str = "month", kind: str = "") -> dict:
    """The Usage pane's numbers. ``windows``: today / 7 days / 30 days /
    everything kept, unfiltered. For ``period`` (PERIODS): ``totals`` and
    the split by kind (``kinds``), every kind; the split by model
    (``models``, provider name + model id + billing — there the billing's
    name, not a split — biggest first) only of ``kind`` ("" = every kind),
    whose own total is ``kinds[kind]``. ``daily``: a 365-day UTC calendar,
    every kind, one counts object per day (``date`` beside it). Every
    counts object carries ``cost`` (the known sum, USD), ``priced_calls``
    and ``unpriced_calls`` (among the calls paid per token) and ``billing``
    (the same counts by how the calls were paid for)."""
    period = period if period in PERIODS else "month"
    with connect_users_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT at, kind, provider_id, provider_name, model, input, output, cache_read, cache_write, "
            "billing, cost FROM ai_usage WHERE user_id = ? ORDER BY at DESC", (user_id,)).fetchall()
    now = datetime.now(timezone.utc)
    day_start = now.strftime("%Y-%m-%dT00:00:00")
    since = {"today": day_start,
             "week": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"),
             "month": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")}
    windows = {name: _totals(r for r in rows if r["at"] >= start) for name, start in since.items()}
    windows["all"] = _totals(rows)
    calendar_start = now.date() - timedelta(days=364)
    daily = {(calendar_start + timedelta(days=i)).isoformat(): _counts() for i in range(365)}
    for row in rows:
        bucket = daily.get(row["at"][:10])
        if bucket is not None:
            _add(bucket, row)
    days = PERIODS[period]
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S") if days else ""
    period_rows = [r for r in rows if r["at"] >= start]
    chosen = [r for r in period_rows if not kind or r["kind"] == kind]
    kinds: dict = {}
    models: dict = {}
    for r in period_rows:
        _add(kinds.setdefault(r["kind"], _counts()), r)
    for r in chosen:
        m = models.setdefault((r["provider_id"], r["model"], r["billing"]),
                              {"provider_id": r["provider_id"], "provider_name": r["provider_name"],
                               "model": r["model"], **_counts()})
        _add(m, r)
    return {
        "period": period,
        "kind": kind,
        "windows": windows,
        "totals": _totals(period_rows),
        "daily": [{"date": date, **_rounded(counts)} for date, counts in daily.items()],
        "kinds": {k: _rounded(v) for k, v in kinds.items()},
        # A model's row is one billing's (its key), so its ``billing`` is that
        # billing's name rather than a split of one.
        "models": sorted(({**_rounded(m), "billing": key[2]} for key, m in models.items()),
                         key=lambda m: -(m["input"] + m["output"])),
        "first_at": rows[-1]["at"] if rows else "",
        "keep_days": KEEP_DAYS,
    }


def clear(user_id: str) -> int:
    """Drop the account's usage rows; returns how many went. The rows the
    shared allowance still counts (shared entries, last 24 hours) stay — a
    reset must not refill the allowance."""
    with connect_users_db() as conn:
        cur = conn.execute(f"DELETE FROM ai_usage WHERE user_id = ? AND NOT ({_METERED})",
                           (user_id, *_metered_args()))
        return cur.rowcount

"""The portal's Plan & billing page (``/plan``), the Overview's plan card and
the Admin page's Billing tab (docs/dev/billing.md). ``pages.admin_page``
includes ``ADMIN_TAB`` and appends ``ADMIN_JS`` to its script.

The Plan page has six states, chosen from the subscription row and the
hosted server (``billing.summary``): free (the four plan cards), checkout
returned (waiting for a Pro server), active, past due, paused, and
cancelled or read-only. A subscriber sees the subscription with its
actions, where the library lives (the shared server for Lite and Plus, a
server of their own for Pro), the billing history and, while the plan can
be changed, the cards again as switches. The page renders from cloud.db alone. Its script reads
Stripe afterwards: ``GET /api/billing/me`` (a stale row) and
``GET /api/billing/details`` (the payment method and the invoices). Every
payment action is a redirect to Stripe: Checkout for a first purchase, the
Customer Portal opened on one flow for a change. Its styles are a
``<style>`` block of its own over the portal's shared classes.
"""

import math
from datetime import datetime, timezone

from . import billing, config, pages


def can_buy(summary: dict, plan: str) -> bool:
    """Whether ``billing.checkout_url`` would accept a checkout of ``plan``
    now: it can be sold (``billing.can_sell``) and the account holds no
    subscription (a held one is changed in the Customer Portal). Every
    Choose and Resume button follows this."""
    sub = summary["subscription"]
    return plan in summary["sells"] and not (sub and sub["status"] in billing.HELD_STATUSES)


def _shared(plan: str) -> bool:
    """The plan's library is an account on the shared server."""
    return bool(config.PLAN_LIMITS.get(plan, {}).get("shared"))


def _can_switch(summary: dict) -> bool:
    """Whether the cards are switches: a paid-up subscription that is not
    set to end (``billing._can_switch``)."""
    sub = summary["subscription"]
    return bool(sub and sub["can_switch"])


# --- the plan cards -----------------------------------------------------------

def _size(mb: int) -> str:
    return f"{mb // 1024} GB" if mb >= 1024 and mb % 1024 == 0 else f"{mb} MB"


def _used(nbytes: int) -> str:
    return f"{nbytes / 1024 ** 3:.1f} GB" if nbytes >= 1024 ** 3 else f"{nbytes / 1024 ** 2:.0f} MB"


def _copies(interval_s: int, keep: int) -> str:
    return f"{'Hourly' if interval_s <= 3600 else 'Daily'} off-site copies, {keep} kept"


def _host(username: str) -> str:
    """The address a Pro server of this account would answer at."""
    return f"{username}{config.HOSTED_SUFFIX}.{config.HOSTED_DOMAIN or 'gammapdf.com'}"


def plan_lines(plan: str, username: str) -> list[str]:
    """The six lines a card lists (HTML), from ``config.PLAN_LIMITS``."""
    lim = config.PLAN_LIMITS[plan]
    if lim.get("shared"):
        where = f" at <b>{pages.esc(pages._bare(config.APP_URL))}</b>" if config.APP_URL else ""
        return [f"Your library on Gamma Cloud, always on{where}",
                "One account; share links (view or edit) still work for anyone",
                f"{_size(lim['quota_mb'])} for uploads, {lim['max_upload_mb']} MB per file",
                "Unlimited published pages",
                "Desktop offline copies, iPad, the browser extension, Codex and Claude Code over MCP",
                "An account on our shared server; Pro is a server of your own"]
    if plan == "pro":
        return [f"A Gamma server of your own at <b>{pages.esc(_host(username))}</b>, which you administer",
                f"{lim['max_accounts']} accounts, shared workspaces with roles and live cursors",
                f"{_size(lim['quota_mb'])} pooled, {lim['max_upload_mb']} MB per file, per-workspace quotas",
                "Invite members by their Gamma Cloud username",
                "One shared AI connection the admin adds for the whole server",
                _copies(lim["offsite_interval_s"], lim["offsite_keep"])]
    return ["The desktop app: your library on your own disk",
            "Self-host a Gamma server for your group, free",
            "5 published pages on the share host",
            "50 MB per file",
            "AI with your own key or a ChatGPT sign-in",
            "No hosted library"]


MINE = "<button class='btn btn--block' disabled{}>Your current plan</button>"
SOON = "<button class='btn btn--block' disabled>Coming soon</button>"


def _button(plan: str, summary: dict, current: bool) -> str:
    """A card's button. For a subscriber who can switch, another plan on
    sale and this plan's other billing period (while it has a price id)
    open Stripe's confirmation of that move (``data-switch``); otherwise a
    paid card starts a checkout (``data-choose``) while one would be
    accepted. A plan that cannot be bought now is *Coming soon*."""
    if plan == "free":
        return MINE.format("") if current else ""
    name, sub = plan.capitalize(), summary["subscription"]
    if _can_switch(summary):
        if sub["plan"] != plan:
            if plan not in summary["sells"]:
                return SOON
            verb = "Upgrade" if config.PLAN_RANK[plan] > config.PLAN_RANK.get(sub["plan"], 0) else "Switch"
            return f"<button class='btn btn--primary btn--block' data-switch={plan}>{verb} to {name}</button>"
        here, there, other = (" data-y", " data-m", "month") if sub["interval"] == "year" else (" data-m", " data-y", "year")
        if not config.STRIPE_PRICES.get(f"{plan}_{other}", ("", "", ""))[2]:
            return MINE.format("")
        return (MINE.format(here)
                + f"<button class='btn btn--block' data-switch={plan}{there}>Switch to {other}ly billing</button>")
    if current:
        return MINE.format("")
    if not can_buy(summary, plan):
        return SOON
    return f"<button class='btn btn--primary btn--block' data-choose={plan}>Choose {name}</button>"


def _card(plan: str, summary: dict, account: dict) -> str:
    current = account["plan"] == plan
    lines = "".join(f"<li>{line}</li>" for line in plan_lines(plan, account["username"]))
    if plan == "free":
        price = "<div class=price>$0</div><div class=save>Free and open source</div>"
    else:
        p = config.PLAN_PRICES_USD[plan]
        saving = p["month"] * 12 - p["year"]
        price = (f"<div class=price><span data-m>${p['month']}<small> a month</small></span>"
                 f"<span data-y>${p['year']}<small> a year</small></span></div>"
                 f"<div class=save><span data-m>or ${p['year']} a year, saving ${saving}</span>"
                 f"<span data-y>${saving} less than monthly</span></div>")
    return (f"<div class='card plancard{' cur' if current else ''}'><h3>{plan.capitalize()}"
            f"{' <span class=pill>your plan</span>' if current else ''}</h3>{price}<ul>{lines}</ul>"
            f"{_button(plan, summary, current)}</div>")


def _cards(summary: dict, account: dict) -> str:
    """The Monthly/Yearly toggle and the four cards. A subscriber's toggle
    starts on the period they pay by."""
    # The toggle names the saving when it is the same number of months on every plan.
    months = {(p["month"] * 12 - p["year"]) // p["month"] for p in config.PLAN_PRICES_USD.values()}
    n = months.pop() if len(months) == 1 else 0
    free_months = f" <span class='pill pill--ok'>{n} months free</span>" if n > 0 else ""
    yearly = _can_switch(summary) and summary["subscription"]["interval"] == "year"
    toggle = ("<div class=interval role=group aria-label='Billing period'>"
              f"<button type=button{'' if yearly else ' class=on'} data-interval=month>Monthly</button>"
              f"<button type=button{' class=on' if yearly else ''} data-interval=year>Yearly{free_months}</button></div>")
    note = ""
    if not summary["sells"]:
        note = ("<p class='empty gap'>"
                + ("Only the Free plan is available right now. Paid plans are coming soon." if account["plan"] == "free"
                   else "Your plan stays as it is. Other plans are coming soon.") + "</p>")
    return (toggle + f"<div class='plans{' y' if yearly else ''}' id=plans>"
            + "".join(_card(p, summary, account) for p in config.PLANS)
            + "</div>" + note + "<p class='empty gap'>Rather run it yourself? The server is free and open source: "
            f"<a href='{pages.SITE}/#selfhost'>self-host Gamma</a> on your own machine or VPS.</p>")


def _fine() -> str:
    """The terms every state of the page ends with."""
    return ("<p class=fine>Payments are handled by Stripe and are not refunded. Cancelling stops the next renewal; "
            "the plan stays active until the end of the period already paid for. A change of plan is confirmed on "
            "Stripe's page, which shows the charge before you agree. Prices are in US dollars, plus tax where it "
            f"applies. <a href='{pages.SITE}/terms/'>Terms</a></p>")


# --- the server and the subscription ------------------------------------------

def _days_left(iso: str | None) -> int:
    t = pages._when(iso or "")
    return max(0, math.ceil((t - datetime.now(timezone.utc)).total_seconds() / 86400)) if t else 0


def _storage(hosted: dict, plan: str) -> str:
    """"1.2 GB of 6 GB" over a meter, from the server's last report; ""
    before the first one."""
    quota = (hosted.get("limits") or {}).get("quota_mb") or config.PLAN_LIMITS.get(plan, {}).get("quota_mb")
    used = (hosted.get("report") or {}).get("uploads_bytes")
    if not quota or used is None:
        return ""
    pct = min(100, round(used / (quota * 1024 * 1024) * 100))
    return f"<span class=progress>{_used(used)} of {_size(int(quota))}<i><b style='width:{pct}%'></b></i></span>"


def _state_pill(hosted: dict) -> str:
    state = hosted.get("state") or ""
    if hosted.get("read_only"):
        return "<span class='pill pill--warn'>read-only</span>"
    return f"<span class='pill{' pill--ok' if state == 'running' else ''}'>{pages.esc(state)}</span>"


def _server(hosted: dict | None, plan: str) -> str:
    """The hosted server's section: address, storage against the cap,
    members on Pro, the off-site copies."""
    if not hosted:
        return ("<section class=section><h2>Your server</h2><div class=body><p class=plantext>"
                "Your server is being set up. This page shows its address once it runs.</p></div></section>")
    url = hosted.get("url") or ""
    limits, report = hosted.get("limits") or {}, hosted.get("report") or {}
    state = hosted.get("state") or ""
    rows = f"<dt>Address</dt><dd><a href='{pages.esc(url)}'>{pages.esc(pages._bare(url))}</a></dd>" if url else ""
    storage = _storage(hosted, plan)
    if storage:
        rows += f"<dt>Storage</dt><dd>{storage}</dd>"
    if plan == "pro":
        cap = limits.get("max_accounts") or config.PLAN_LIMITS["pro"]["max_accounts"]
        members = report.get("accounts")
        rows += f"<dt>Members</dt><dd>{'' if members is None else f'{int(members)} of '}{int(cap)}</dd>"
    offsite = limits.get("offsite") or {}
    if offsite.get("interval_s") and offsite.get("keep"):
        rows += f"<dt>Backups</dt><dd>{_copies(offsite['interval_s'], offsite['keep'])}</dd>"
    if hosted.get("reported_at"):
        rows += f"<dt>Last report</dt><dd>{pages._date(hosted['reported_at'])}</dd>"
    invite = ("<p class='plantext gap'>Invite people on your server by their Gamma Cloud username; members "
              "sign in with their own Gamma Cloud account.</p>") if plan == "pro" else ""
    open_btn = (f"<div class=actions><a class='btn btn--primary btn--sm' href='{pages.esc(url)}'>Open</a></div>"
                if url and state not in ("provisioning", "stopped", "deleted") else "")
    return (f"<section class=section><h2>Your server {_state_pill(hosted)}</h2><div class=body><dl class=kv>{rows}</dl>"
            f"{invite}{open_btn}</div></section>")


def _library(summary: dict, plan: str) -> str:
    """Where a Lite or Plus library lives: the shared server's address, the
    plan's allowance there, and Open. Open goes through that server's
    cloud sign-in, so the session it starts carries the plan as it is now
    (the server otherwise learns of a change within the hour)."""
    url, limits = summary["shared_url"], config.shared_limits(plan)
    if not url or not limits:
        return ""
    rows = (f"<dt>Address</dt><dd><a href='{pages.esc(url)}'>{pages.esc(pages._bare(url))}</a></dd>"
            f"<dt>Storage</dt><dd>{_size(limits['quota_mb'])}, {limits['max_upload_mb']} MB per file</dd>")
    return ("<section class=section><h2>Your library</h2><div class=body>"
            f"<dl class=kv>{rows}</dl><p class='plantext gap'>An account on Gamma Cloud's shared server. Sign in "
            "there with this account; the desktop app, the extension and your assistants connect to that address.</p>"
            f"<div class=actions><a class='btn btn--primary btn--sm' href='{pages.esc(url)}/api/auth/cloud/start?next=/'>"
            "Open</a></div></div></section>")


def _home(summary: dict, plan: str) -> str:
    """Where the plan's library lives: the shared server for Lite and Plus,
    the account's own server for Pro. A server that outlived its plan (a
    move down from Pro) is shown under the library until it is deleted."""
    hosted = summary["hosted"]
    if not _shared(plan):
        return _server(hosted, plan)
    return _library(summary, plan) + (_server(hosted, plan) if hosted and hosted.get("state") != "deleted" else "")


def _money(sub: dict) -> str:
    if sub.get("amount_usd") is None:
        return ""
    return f"${sub['amount_usd']} a {sub['interval']}"


STATUS_WORDS = {"active": "Active", "trialing": "Trial", "past_due": "Payment failed", "paused": "Paused",
                "unpaid": "Unpaid", "canceled": "Ended", "incomplete": "Not paid yet", "incomplete_expired": "Ended"}


def _terms_line(sub: dict) -> str:
    """"Renews 3 Nov 2026 · $5 a month" / "Ends 3 Nov 2026", for the
    Overview's plan card."""
    if sub["cancel_at_period_end"] and sub["period_end"]:
        return f"Ends {pages._date(sub['period_end'])}; it does not renew"
    if sub["renews_at"]:
        return f"Renews {pages._date(sub['renews_at'])}" + (f" · {_money(sub)}" if _money(sub) else "")
    return STATUS_WORDS.get(sub["status"], sub["status"])


def _subscription(sub: dict, summary: dict) -> str:
    """The plan, its price, the next payment or the end, the payment method
    (filled in by the script) and the actions, each a Stripe page of its
    own: change, payment method, cancel; or Keep my plan for one set to end."""
    name, status = sub["plan"].capitalize(), sub["status"]
    ending = sub["cancel_at_period_end"]
    pill = ("<span class='pill pill--warn'>Ending</span>" if ending else
            f"<span class='pill {'pill--ok' if status in ('active', 'trialing') else 'pill--warn'}'>"
            f"{pages.esc(STATUS_WORDS.get(status, status.replace('_', ' ')))}</span>")
    period = {"year": "billed yearly", "month": "billed monthly"}.get(sub["interval"], "")
    rows = f"<dt>Price</dt><dd>{_money(sub)}</dd>" if _money(sub) else ""
    if ending and sub["period_end"]:
        rows += f"<dt>Ends</dt><dd>{pages._date(sub['period_end'])} · does not renew</dd>"
    elif sub["renews_at"]:
        rows += f"<dt>Next payment</dt><dd>{pages._date(sub['renews_at'])}</dd>"
    rows += "<dt>Payment method</dt><dd id=pm>…</dd>"
    if sub["plan"] == "pro" and sub["seats"] > 1:
        rows += f"<dt>Seats</dt><dd>{sub['seats']}</dd>"
    if summary["plan_source"] == "granted":
        rows += f"<dt>Granted</dt><dd>{pages.esc(summary['granted_plan'].capitalize())}, by the operator</dd>"
    pay = "<button class='btn btn--sm' data-portal=payment>Update payment method</button>"
    if status == "paused":
        actions = "<button class='btn btn--primary btn--sm' data-portal>Manage billing</button>"
    elif ending:
        actions = "<button class='btn btn--primary btn--sm' data-keep>Keep my plan</button>" + pay
    else:
        change = "<a class='btn btn--sm' href='#plans'>Change plan</a>" if sub["can_switch"] else ""
        actions = change + pay + "<button class='btn btn--sm btn--danger' data-portal=cancel>Cancel plan</button>"
    after = ("your library closes" if _shared(sub["plan"])
             else "your server turns read-only")
    ends = (f"<p class='plantext gap'>Your plan ends with this period and {after} then. "
            "Keep my plan lets it renew as before.</p>" if ending else "")
    return (f"<section class=section><h2>Subscription {pill}</h2><div class=body>"
            f"<div class=planname>{name}{f' <small>{period}</small>' if period else ''}</div>"
            f"<dl class=kv>{rows}</dl>{ends}<div class=actions>{actions}</div></div></section>")


def _history() -> str:
    """The invoices table; the script fills it from ``/api/billing/details``."""
    return ("<section class=section id=history><h2>Billing history <span><button class=linkbtn data-portal>"
            "All invoices and billing details</button></span></h2><div class='body tbl'><table><thead><tr>"
            "<th>Date</th><th>Description</th><th>Amount</th><th>Status</th><th></th></tr></thead>"
            "<tbody id=invoices><tr><td colspan=5 class=empty>Loading…</td></tr></tbody></table></div></section>")


def _strip(text: str, button: str = "", bad: bool = False) -> str:
    return f"<div class='notice{' bad' if bad else ''}'><span>{text}</span>{button}</div>"


FIX = "<button class='btn btn--sm' data-portal>Fix payment</button>"
TO_PLAN = "<a class='btn btn--sm' href='/plan'>Plan and billing</a>"


def _past_due(sub: dict, button: str = FIX) -> str:
    shared = _shared(sub["plan"])
    if sub["grace_ends_at"] and _days_left(sub["grace_ends_at"]) > 0:
        what = "Your plan ends" if shared else "Your server becomes read-only"
        text = (f"<b>Your last payment failed.</b> {what} on "
                f"{pages._date(sub['grace_ends_at'])} unless a payment goes through.")
    else:
        what = "your plan is on hold" if shared else "your server is read-only"
        text = f"<b>Your last payment failed</b> and the grace period is over: {what} until it is paid."
    return _strip(text, button, bad=True)


def _paused(sub: dict, button: str = "<button class='btn btn--sm' data-portal>Manage billing</button>") -> str:
    """Stripe paused the subscription (a trial ended without a payment
    method, or the operator paused collection): the portal resumes it; a
    new checkout would be refused while it is held."""
    what = "is on hold" if _shared(sub["plan"]) else "does not renew and your server is read-only"
    return _strip(f"<b>Payment collection is paused.</b> Your plan {what} until it resumes. "
                  "Add a payment method or resume it under Manage billing.", button, bad=True)


READ_ONLY = ("<b>Your server is read-only.</b> Writes are refused until the plan is paid again; "
             "reading and export work.")


def _ended(sub: dict, summary: dict, hosted: dict | None) -> str:
    """Cancelled, or unpaid after Stripe's retries: what becomes of the
    library, and Resume (a new checkout) or Fix payment. A server of the
    account's own says when it stops and is deleted (its own dates,
    ``hosted.status_for``) and offers Export; a library on the shared
    server is kept but closed, since that server signs in only an account
    on a plan (``accounts.on_shared``)."""
    hosted = hosted or {}
    url = hosted.get("url") or ""
    if sub["status"] == "unpaid":
        head = "<b>Your payments failed and the retries ran out.</b> Pay the open invoice to resume your plan."
        action = FIX
    else:
        head = (f"<b>Your {pages.esc(sub['plan'].capitalize())} plan ended</b>"
                + (f" on {pages._date(sub['ended_at'])}." if sub["ended_at"] else "."))
        price = f"{sub['plan']}_{sub['interval'] or 'month'}"
        action = (f"<button class='btn btn--primary btn--sm' data-resume='{pages.esc(price)}'>Resume</button>"
                  if can_buy(summary, sub["plan"]) else "")
    if _shared(sub["plan"]) and not hosted:
        return (_strip(head, "", bad=True)
                + "<section class=section><h2>What happens to your library</h2><div class=body><p class=plantext>"
                "Everything in it is kept where it is, but it is closed: you cannot sign in to it without a plan. "
                "Resume the plan to open it again as it was. Pages you published stay online.</p>"
                f"<div class=actions>{action}</div></div></section>")
    rows = ""
    if hosted.get("stops_at"):
        left = _days_left(hosted["stops_at"])
        rows += (f"<dt>Readable until</dt><dd>{pages._date(hosted['stops_at'])}"
                 f"{f' · {left} days left' if left else ''}</dd>")
    if hosted.get("deletes_at"):
        rows += f"<dt>Deleted on</dt><dd>{pages._date(hosted['deletes_at'])}</dd>"
    export = (f"<a class='btn btn--sm' href='{pages.esc(url.rstrip('/'))}/?settings=backups'>Export</a>"
              if url and hosted.get("state") == "read_only" else "")
    text = ("<p class=plantext>Your library stays readable for a while: export it, or resume to keep "
            "working on it. After that the server is stopped, then deleted with its files and copies.</p>")
    return (_strip(head, "", bad=True)
            + f"<section class=section><h2>What happens to your server</h2><div class=body>{text}"
            + (f"<dl class='kv gap'>{rows}</dl>" if rows else "")
            + f"<div class=actions>{export}{action}</div></div></section>")


# --- the page -----------------------------------------------------------------

STYLE = """<style>
.plans{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:12px;scroll-margin-top:72px}
.plancard{padding:18px;display:flex;flex-direction:column;gap:8px}
.plancard.cur{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.plancard h3{font-size:16px;display:flex;align-items:center;gap:8px}.plancard .price{font-size:26px;font-weight:600;letter-spacing:-.02em}
.plancard .price small{font-size:13px;font-weight:400;color:var(--muted);letter-spacing:0}.plancard .save{font-size:12.5px;color:var(--muted);min-height:1.3em}
.plancard ul{margin:4px 0 6px;padding-left:18px;color:var(--text-2);font-size:13.5px;flex:1}.plancard li+li{margin-top:5px}
.interval{display:inline-flex;border:1px solid var(--line-2);border-radius:8px;padding:2px;margin-bottom:14px;background:var(--surface)}
.interval button{background:none;border:0;padding:5px 12px;border-radius:6px;font:inherit;font-weight:500;color:var(--text-2);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.interval button.on{background:var(--surface-2);color:var(--text)}
.plans:not(.y) [data-y],.plans.y [data-m]{display:none}
.subhead{font-size:15px;margin:28px 0 12px}
#history .tbl{overflow-x:auto}#history td{white-space:nowrap}#history td.what{white-space:normal;min-width:180px}#history td.links{text-align:right}#history td.links a+a{margin-left:12px}
#bmsg{margin:0 0 12px}#bmsg:empty{display:none}
@media(max-width:1100px){.plans{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:820px){.plans{grid-template-columns:1fr}}
</style>"""

SCRIPT = """
const bmsg = document.getElementById('bmsg'), plans = document.getElementById('plans');
let interval = plans && plans.classList.contains('y') ? 'year' : 'month';
document.querySelectorAll('[data-interval]').forEach(b => b.onclick = () => {
  interval = b.dataset.interval;
  document.querySelectorAll('[data-interval]').forEach(x => x.classList.toggle('on', x === b));
  if (plans) plans.classList.toggle('y', interval === 'year');
});
// Every payment action leaves for a page at Stripe and comes back to /plan.
const go = (b, path, body) => act(b, async () => { const d = await api(path, body); location.href = d.url; }, bmsg);
document.querySelectorAll('[data-choose]').forEach(b => b.onclick = () => go(b, '/api/billing/checkout', {price: b.dataset.choose + '_' + interval}));
document.querySelectorAll('[data-resume]').forEach(b => b.onclick = () => go(b, '/api/billing/checkout', {price: b.dataset.resume}));
document.querySelectorAll('[data-switch]').forEach(b => b.onclick = () => go(b, '/api/billing/portal', {flow: 'switch', price: b.dataset.switch + '_' + interval}));
document.querySelectorAll('[data-portal]').forEach(b => b.onclick = () => go(b, '/api/billing/portal', {flow: b.dataset.portal}));
document.querySelectorAll('[data-keep]').forEach(b => b.onclick = () => act(b, async () => { await api('/api/billing/keep', {}); location.href = '/plan?billing=keep'; }, bmsg));
const setup = document.getElementById('setup');
if (setup) {
  const started = Date.now(), done = (id) => { const s = document.getElementById(id); s.classList.remove('now'); s.classList.add('done'); };
  const poll = async () => {
    try {
      const s = (await api('/api/hosted/status', undefined, 'GET')).server;
      if (s && s.state === 'running' && s.url) {
        done('setup2'); done('setup3');
        document.getElementById('setuphead').textContent = 'Your server is ready';
        document.getElementById('setuptext').textContent = 'It runs at ' + s.url.replace(/^https?:\\/\\//, '') + '. Sign in there with this account.';
        const o = document.getElementById('open'); o.href = s.url; o.hidden = false; return;
      }
    } catch (e) { if (e.status === 401) { location.href = '/login?next=/plan'; return; } }
    if (Date.now() - started > 15 * 60 * 1000) { bmsg.textContent = 'This is taking longer than it should. Reload in a few minutes, or write to support.'; return; }
    setTimeout(poll, 3000);
  };
  poll();
}
// The payment method and the invoices come from Stripe, after the page: it never waits for them.
const invoices = document.getElementById('invoices'), pm = document.getElementById('pm');
const INVOICE = {paid: ['Paid', ' pill--ok'], open: ['Due', ' pill--warn'], uncollectible: ['Unpaid', ' pill--warn'], void: ['Void', '']};
const money = (n, cur) => { try { return (n / 100).toLocaleString(undefined, {style: 'currency', currency: cur.toUpperCase()}); } catch (e) { return (n / 100).toFixed(2) + ' ' + cur.toUpperCase(); } };
const link = (url, text, cls) => String(url).startsWith('https://') ? '<a' + (cls ? ' class="' + cls + '"' : '') + ' href="' + esc(url) + '" target=_blank rel="noopener noreferrer">' + text + '</a>' : '';
function method(m){
  if (!m) return 'None on file';
  const words = m.kind.replace(/_/g, ' '), name = m.kind === 'card' && m.brand ? m.brand : words;
  return name.charAt(0).toUpperCase() + name.slice(1) + (m.last4 ? ' •••• ' + m.last4 : '')
    + (m.exp_month && m.exp_year ? ' · ' + String(m.exp_month).padStart(2, '0') + '/' + String(m.exp_year).slice(-2) : '');
}
if (invoices || pm) api('/api/billing/details', undefined, 'GET').then(d => {
  if (pm) pm.textContent = method(d.payment_method);
  if (invoices) invoices.innerHTML = d.invoices.map(i => { const st = INVOICE[i.status] || [i.status, ''];
    return '<tr><td>' + esc(fmtDay(i.created)) + '</td><td class=what>' + esc(i.description || 'Gamma Cloud') + (i.number ? '<br><span class=mono>' + esc(i.number) + '</span>' : '') + '</td>'
      + '<td>' + esc(money(i.total, i.currency)) + '</td><td><span class="pill' + st[1] + '">' + esc(st[0]) + '</span></td>'
      + '<td class=links>' + (i.status === 'open' ? link(i.url, 'Pay', 'btn btn--sm btn--primary') : link(i.url, 'Receipt')) + link(i.pdf, 'PDF') + '</td></tr>'; }).join('')
    || '<tr><td colspan=5 class=empty>No invoices yet.</td></tr>';
}).catch(e => {
  if (e.status === 401) { location.href = '/login?next=/plan'; return; }
  if (pm) pm.textContent = 'Not available right now';
  if (invoices) invoices.innerHTML = '<tr><td colspan=5 class=empty>The billing history is not available right now. Reload in a minute.</td></tr>';
});
"""


def _step(el_id: str, cls: str, title: str, sub: str) -> str:
    return (f"<li class='step {cls}' id={el_id}><span class=dot>{pages.ICONS['check']}</span>"
            f"<span class=txt><b>{title}</b><span>{sub}</span></span></li>")


def _setup(account: dict) -> str:
    """Back from Checkout, until the server runs: three steps the script
    ticks off as ``GET /api/hosted/status`` moves."""
    host = pages.esc(_host(account["username"]))
    return ("<section class=section><h2 id=setuphead>Setting up your server…</h2><ul class=steps id=setup>"
            + _step("setup1", "done", "Payment received", "Thank you. Your plan is active.")
            + _step("setup2", "now", "Creating your server",
                    f"<b>{host}</b> is being created; this takes a minute or two.")
            + _step("setup3", "", "Ready", "<span id=setuptext>This page updates by itself.</span>")
            + "</ul><div class=body><div class=actions><a class='btn btn--primary btn--sm' id=open href='#' hidden>Open</a>"
            "<a class='btn btn--sm' href='/plan'>Back to your plan</a></div></div></section>")


def plan_state(summary: dict, checkout: str = "") -> str:
    """``setup`` / ``active`` / ``past_due`` / ``paused`` / ``ended`` /
    ``free``. ``?checkout=success`` means "setting up" only for an account
    Stripe knows (a subscription row or a customer) whose server does not
    run yet; on any other account the parameter is ignored, and so it is
    for a plan on the shared server, which has nothing to set up."""
    sub, hosted = summary["subscription"], summary["hosted"]
    known = bool(sub) or summary.get("has_customer")
    waits = not sub or config.PLAN_LIMITS.get(sub["plan"], {}).get("hosted")
    if checkout == "success" and known and waits and not (hosted and hosted.get("state") == "running"):
        return "setup"
    if sub and sub["status"] == "past_due":
        return "past_due"
    if sub and sub["status"] in ("active", "trialing"):
        return "active"
    if sub and sub["status"] == "paused":
        return "paused"
    if sub and sub["status"] in ("canceled", "unpaid", "incomplete_expired"):
        return "ended"
    return "free"


# What the page says when Stripe's portal sends the browser back (``?billing=``).
RETURNED = {"payment": "Your payment method is saved.",
            "cancel": "Your plan is set to end. It stays active until the end of the period you paid for.",
            "switch": "Your plan change is saved. It can take a minute to show here.",
            "keep": "Your plan renews again as before."}


def plan_page(account: dict, summary: dict, checkout: str = "", returned: str = "") -> str:
    """``account``: ``accounts.public``; ``summary``: ``billing.summary``;
    ``checkout``: the ``?checkout=`` Stripe's Checkout sent the browser back
    with; ``returned``: the ``?billing=`` of a finished portal flow."""
    account = {**account, "plan": summary["plan"]}
    state = plan_state(summary, checkout)
    sub, hosted = summary["subscription"], summary["hosted"]
    plan = summary["plan"]
    read_only = _strip(READ_ONLY, "", bad=True) if hosted and hosted.get("read_only") and state != "ended" else ""
    history = _history() if summary["enabled"] and summary["has_customer"] else ""
    saved = _strip(f"<b>Done.</b> {RETURNED[returned]}") if returned in RETURNED else ""
    if state == "setup":
        inner = _setup(account)
    elif state in ("active", "past_due", "paused"):
        thanks = _strip("<b>Thank you.</b> Your plan is active.") if checkout == "success" and state == "active" else ""
        strip = _past_due(sub) if state == "past_due" else _paused(sub) if state == "paused" else read_only
        cards = ("<h2 class=subhead>Change plan</h2>" + _cards(summary, account)) if sub["can_switch"] else ""
        inner = (thanks + saved + strip
                 + f"<div class=cols><div>{_subscription(sub, summary)}</div><div>{_home(summary, sub['plan'])}</div></div>"
                 + history + cards)
    elif state == "ended":
        # unpaid is still held: it is paid in the portal, so no plan cards to buy from
        inner = (_ended(sub, summary, hosted) + history
                 + (_cards(summary, account) if sub["status"] not in billing.HELD_STATUSES else ""))
    else:
        granted = ""
        if summary["plan_source"] == "granted":
            granted = _strip(f"<b>{pages.esc(plan.capitalize())} plan, granted.</b> The Gamma Cloud operator gave this "
                             "account its plan; there is nothing to pay.")
        home = _home(summary, plan) if plan != "free" or hosted else ""
        server = f"<div class=cols><div>{home}</div><div></div></div>" if home else ""
        # Stripe's webhook may not have arrived yet; nothing here can tell a real return from a typed URL
        pending = (_strip("If you have just paid, your plan shows here within a minute. Reload this page.")
                   if checkout == "success" else "")
        inner = pending + granted + read_only + server + _cards(summary, account) + history
    lead = ("The desktop app is free. A paid plan adds a library that is always on: an account on Gamma Cloud, "
            "or with Pro a server of your own." if state in ("free", "ended")
            else "Your plan, its billing and where your library lives.")
    script = SCRIPT
    if sub:
        # The page never calls Stripe. This read refreshes a stale row (at once after a portal flow)
        # and reloads when what the page shows has moved.
        shown = [plan, sub["status"], sub["interval"], sub["cancel_at_period_end"], sub["period_end"]]
        script += (f"const SHOWN = {pages._js(shown)};\n"
                   f"api('/api/billing/me{'?fresh=1' if returned else ''}', undefined, 'GET').then(d => {{ const s = d.subscription || {{}}; "
                   "if (JSON.stringify([d.plan, s.status, s.interval, s.cancel_at_period_end, s.period_end]) !== "
                   "JSON.stringify(SHOWN)) location.reload(); }).catch(() => {});")
    return pages.app("Plan & billing", lead, account, "plan",
                     STYLE + "<div class=msg id=bmsg></div>" + inner + _fine(), script)


# --- the Overview -------------------------------------------------------------

def overview_alert(summary: dict) -> str:
    """The strip the Overview shows while the plan needs the person: a
    failed payment, a paused or ended subscription, a read-only server."""
    sub, hosted = summary["subscription"], summary["hosted"]
    state = plan_state(summary)
    if state == "past_due":
        return _past_due(sub, TO_PLAN)
    if state == "paused":
        return _paused(sub, TO_PLAN)
    if hosted and hosted.get("state") in ("read_only", "stopped"):
        gone = f" It is deleted on {pages._date(hosted['deletes_at'])}." if hosted.get("deletes_at") else ""
        return _strip(f"<b>Your server is {'stopped' if hosted['state'] == 'stopped' else 'read-only'}</b> "
                      f"because its plan ended.{gone}", TO_PLAN, bad=True)
    return ""


def overview_plan(account: dict, summary: dict) -> str:
    """The Overview's plan card: the plan, how it renews or ends, the
    hosted server's address and storage, and the way to the Plan page."""
    plan, sub, hosted = summary["plan"], summary["subscription"], summary["hosted"]
    if plan == "free":
        text = "The desktop app is your library. A paid plan adds one that is always on."
    elif summary["plan_source"] == "granted":
        text = "Granted by the Gamma Cloud operator; there is nothing to pay."
    elif sub:
        text = _terms_line(sub)
    else:
        text = ""
    if plan == "free" and sub and sub["status"] in ("canceled", "incomplete_expired"):
        text = f"Your {pages.esc(sub['plan'].capitalize())} plan ended. Resume it to open your library again."
    rows = ""
    if _shared(plan) and summary["shared_url"]:
        url = summary["shared_url"]
        rows += f"<dt>Library</dt><dd><a href='{pages.esc(url)}'>{pages.esc(pages._bare(url))}</a></dd>"
    if hosted and hosted.get("state") != "deleted":
        url = hosted.get("url") or ""
        if url:
            rows += f"<dt>Server</dt><dd><a href='{pages.esc(url)}'>{pages.esc(pages._bare(url))}</a></dd>"
        storage = _storage(hosted, plan)
        if storage:
            rows += f"<dt>Storage</dt><dd>{storage}</dd>"
    return (f"<section class=section><h2>Plan{' ' + _state_pill(hosted) if hosted and rows else ''}</h2><div class=body>"
            f"<div class=planname>{pages.esc(plan)}</div><p class=plantext>{text}</p>"
            + (f"<dl class='kv gap'>{rows}</dl>" if rows else "")
            + "<div class=actions><a class='btn btn--primary btn--sm' href='/plan'>Plan and billing</a>"
            + ("" if plan != "free" else f"<a class='btn btn--sm' href='{pages.SITE}/#selfhost'>Self-host instead</a>")
            + "</div></div></section>")


# --- the Admin page's Billing tab ---------------------------------------------

STATUSES = ("active", "trialing", "past_due", "unpaid", "canceled", "incomplete", "incomplete_expired", "paused")

ADMIN_TAB = (
    "<div id=tab-billing hidden><div class=notice id=boff hidden><span>Billing is off: "
    "<code>GAMMA_CLOUD_STRIPE_SECRET</code> is not set.</span></div><div class=tiles id=bsum></div>"
    "<div class=section><h2>Subscriptions <span><span id=bcount></span><select id=bstatus class=sm aria-label='Status'>"
    "<option value=''>every status</option>"
    + "".join(f"<option value={s}>{s.replace('_', ' ')}</option>" for s in STATUSES)
    + "</select></span></h2><div class='body tbl'><table><thead><tr><th>Account</th><th>Plan</th><th>Status</th>"
    "<th>Period end</th><th>Cancels</th><th>Stripe customer</th><th></th></tr></thead><tbody id=subs></tbody></table></div></div>"
    "<div class=section><h2>Webhook events <span>the newest 50 deliveries from Stripe and what each did</span></h2>"
    "<div class='body tbl'><table><thead><tr><th>Received</th><th>Event</th><th>Account</th><th>Outcome</th></tr></thead>"
    "<tbody id=bevents></tbody></table></div></div>"
    "<p class=empty>Refunds, disputes and invoices stay in Stripe's dashboard; whatever changes there comes back "
    "through the webhook. The plan select on the Accounts tab is a courtesy grant: it never ends a subscription, "
    "and the higher of the two is the plan.</p></div>")

ADMIN_JS = """
// --- Billing tab (pages_billing.py) ---
async function loadBilling(){
  const sel = document.getElementById('bstatus');
  sel.onchange = () => loadBilling();
  const [d, ev] = await Promise.all([api('/api/admin/subscriptions?status=' + encodeURIComponent(sel.value), undefined, 'GET'),
                                     api('/api/admin/billing-events', undefined, 'GET')]);
  const s = d.summary, tile = fleetUI.tile;
  document.getElementById('boff').hidden = d.enabled;
  document.getElementById('bsum').innerHTML = tile('Paying', s.paying, esc(Object.entries(s.by_plan).map(([p, n]) => n + ' ' + p).join(' · ')))
    + tile('A month', '$' + s.mrr_usd.toFixed(2), 'a yearly plan counts a twelfth')
    + tile('Payment failed', s.past_due, 'in grace or past it') + tile('Ending', s.ending, 'cancelled, still in their period');
  document.getElementById('bcount').textContent = d.subscriptions.length + ' shown';
  document.getElementById('subs').innerHTML = d.subscriptions.map(s => '<tr><td><b>' + esc(s.username || '(deleted)') + '</b><br><span class=mono>' + esc(s.email || s.account_id) + '</span></td>'
    + '<td>' + esc(s.plan) + (s.effective_plan && s.effective_plan !== s.plan ? ' <span class=pill>' + esc(s.effective_plan) + ' effective</span>' : '') + '</td>'
    + '<td><span class="pill' + (s.status === 'active' || s.status === 'trialing' ? ' pill--ok' : ' pill--warn') + '">' + esc(s.status.replace('_', ' ')) + '</span></td>'
    + '<td>' + esc((s.current_period_end || '').slice(0, 10)) + '</td><td>' + (s.cancel_at_period_end ? 'at period end' : '') + '</td>'
    + '<td>' + (s.stripe_customer_id ? '<a class=mono href="https://dashboard.stripe.com' + (d.test_mode ? '/test' : '') + '/customers/' + encodeURIComponent(s.stripe_customer_id) + '" target=_blank rel=noopener>' + esc(s.stripe_customer_id) + '</a>' : '') + '</td>'
    + '<td><button class="btn btn--sm" data-bref="' + esc(s.account_id) + '"' + (d.enabled && s.stripe_subscription_id ? '' : ' disabled') + '>Refresh</button></td></tr>').join('')
    || '<tr><td colspan=7 class=empty>No subscriptions.</td></tr>';
  document.getElementById('bevents').innerHTML = ev.events.map(e => '<tr><td class=mono>' + esc(e.received_at.slice(0, 19).replace('T', ' ')) + '</td><td class=mono>' + esc(e.type) + '</td>'
    + '<td>' + esc(e.username || e.account_id) + '</td><td><span class="pill' + (e.outcome === 'applied' ? ' pill--ok' : e.outcome === 'mismatch' || e.outcome === 'unknown' ? ' pill--warn' : '') + '">' + esc(e.outcome) + '</span></td></tr>').join('')
    || '<tr><td colspan=4 class=empty>No events yet.</td></tr>';
  document.querySelectorAll('[data-bref]').forEach(b => b.onclick = () => act(b, async () => { await api('/api/admin/subscriptions/' + b.dataset.bref + '/refresh', {}); await loadBilling(); }));
}
"""

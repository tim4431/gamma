"""The portal's Plan page (``/plan``) and the Admin page's Billing tab
(docs/dev/billing.md). ``pages.admin_page`` includes ``ADMIN_TAB`` and
appends ``ADMIN_JS`` to its script.

The Plan page has six states, chosen from the subscription row and the
hosted server (``billing.summary``): free (the three plan cards), checkout
returned (waiting for the server), active, past due, paused, and cancelled
or read-only. It renders from cloud.db alone; Stripe is read by
``GET /api/billing/me``, which the page's script calls after load. Its
styles are a ``<style>`` block of its own over the portal's shared classes.
"""

import math
from datetime import datetime, timezone

from . import billing, config, pages


def can_buy(summary: dict) -> bool:
    """Whether ``billing.checkout_url`` would accept a checkout now: it is
    open and the account holds no subscription (a held one is changed in the
    Customer Portal). Every Choose and Resume button follows this."""
    sub = summary["subscription"]
    return summary["checkout_open"] and not (sub and sub["status"] in billing.HELD_STATUSES)


# --- the plan cards -----------------------------------------------------------

def _size(mb: int) -> str:
    return f"{mb // 1024} GB" if mb >= 1024 and mb % 1024 == 0 else f"{mb} MB"


def _copies(limits: dict) -> str:
    every = "Hourly" if limits["offsite_interval_s"] <= 3600 else "Daily"
    return f"{every} off-site copies, {limits['offsite_keep']} kept"


def _host(username: str) -> str:
    return f"{username}.{config.HOSTED_DOMAIN or 'gammapdf.com'}"


def plan_lines(plan: str, username: str) -> list[str]:
    """The six lines a card lists (HTML), from ``config.PLAN_LIMITS``."""
    if plan == "plus":
        lim = config.PLAN_LIMITS["plus"]
        return [f"A hosted Gamma of your own at <b>{pages.esc(_host(username))}</b>",
                "One account; share links (view or edit) still work for anyone",
                f"{_size(lim['quota_mb'])} for uploads, {lim['max_upload_mb']} MB per file",
                "Unlimited published pages",
                "Desktop offline copies, iPad, the browser extension, Codex and Claude Code over MCP",
                _copies(lim)]
    if plan == "pro":
        lim = config.PLAN_LIMITS["pro"]
        return ["Everything in Plus, for a group",
                f"{lim['max_accounts']} accounts, shared workspaces with roles and live cursors",
                f"{_size(lim['quota_mb'])} pooled, {lim['max_upload_mb']} MB per file, per-workspace quotas",
                "Invite members by their Gamma Cloud username",
                "One shared AI connection the admin adds for the whole server",
                _copies(lim)]
    return ["The desktop app: your library on your own disk",
            "Self-host a Gamma server for your group, free",
            "5 published pages on the share host",
            "50 MB per file",
            "AI with your own key or a ChatGPT sign-in",
            "No hosted server"]


def _card(plan: str, summary: dict, account: dict) -> str:
    name = plan.capitalize()
    current = account["plan"] == plan
    lines = "".join(f"<li>{line}</li>" for line in plan_lines(plan, account["username"]))
    if plan == "free":
        price = "<div class=price>$0</div><div class=save>Free and open source</div>"
        button = "<button class='btn btn--block' disabled>Your current plan</button>" if current else ""
    else:
        p = config.PLAN_PRICES_USD[plan]
        saving = p["month"] * 12 - p["year"]
        price = (f"<div class=price><span data-m>${p['month']}<small> a month</small></span>"
                 f"<span data-y>${p['year']}<small> a year</small></span></div>"
                 f"<div class=save><span data-m>or ${p['year']} a year, saving ${saving}</span>"
                 f"<span data-y>${saving} less than monthly</span></div>")
        if current:
            button = "<button class='btn btn--block' disabled>Your current plan</button>"
        elif not can_buy(summary):
            button = "<button class='btn btn--block' disabled>Not available yet</button>"
        else:
            button = f"<button class='btn btn--primary btn--block' data-choose={plan}>Choose {name}</button>"
    return (f"<div class='card plancard{' cur' if current else ''}'><h3>{name}"
            f"{' <span class=pill>your plan</span>' if current else ''}</h3>{price}<ul>{lines}</ul>{button}</div>")


def _cards(summary: dict, account: dict) -> str:
    # The toggle names the saving when it is the same number of months on every plan.
    months = {(p["month"] * 12 - p["year"]) // p["month"] for p in config.PLAN_PRICES_USD.values()}
    n = months.pop() if len(months) == 1 else 0
    free_months = f" <span class='pill pill--ok'>{n} months free</span>" if n > 0 else ""
    toggle = ("<div class=interval role=group aria-label='Billing period'>"
              "<button type=button class=on data-interval=month>Monthly</button>"
              f"<button type=button data-interval=year>Yearly{free_months}</button></div>")
    note = "" if summary["checkout_open"] else "<p class='empty gap'>Paid plans are not open yet.</p>"
    return (toggle + note + "<div class=plans id=plans>" + "".join(_card(p, summary, account) for p in config.PLANS)
            + "</div><p class='empty gap'>Rather run it yourself? The server is free and open source: "
            f"<a href='{pages.SITE}/#selfhost'>self-host Gamma</a> on your own machine or VPS.</p>")


# --- the server and the subscription ------------------------------------------

def _days_left(iso: str | None) -> int:
    t = pages._when(iso or "")
    return max(0, math.ceil((t - datetime.now(timezone.utc)).total_seconds() / 86400)) if t else 0


def _server(hosted: dict | None, plan: str) -> str:
    """The hosted server's section: address, storage against the cap,
    members on Pro."""
    if not hosted:
        return ("<section class=section><h2>Your server</h2><div class=body><p class=plantext>"
                "Your server is being set up. This page shows its address once it runs.</p></div></section>")
    url = hosted.get("url") or ""
    limits, report = hosted.get("limits") or {}, hosted.get("report") or {}
    state = hosted.get("state") or ""
    pill = ("<span class='pill pill--warn'>read-only</span>" if hosted.get("read_only")
            else f"<span class='pill{' pill--ok' if state == 'running' else ''}'>{pages.esc(state)}</span>")
    rows = f"<dt>Address</dt><dd><a href='{pages.esc(url)}'>{pages.esc(url.split('//')[-1])}</a></dd>" if url else ""
    quota = limits.get("quota_mb") or config.PLAN_LIMITS.get(plan, {}).get("quota_mb")
    used = report.get("uploads_bytes")
    if quota and used is not None:
        pct = min(100, round(used / (quota * 1024 * 1024) * 100))
        rows += (f"<dt>Storage</dt><dd><span class=progress>{used / 1024 ** 3:.1f} of {_size(int(quota))}"
                 f"<i><b style='width:{pct}%'></b></i></span></dd>")
    if plan == "pro":
        cap = limits.get("max_accounts") or config.PLAN_LIMITS["pro"]["max_accounts"]
        members = report.get("accounts")
        rows += f"<dt>Members</dt><dd>{'' if members is None else f'{int(members)} of '}{int(cap)}</dd>"
    if hosted.get("reported_at"):
        rows += f"<dt>Last report</dt><dd>{pages._date(hosted['reported_at'])}</dd>"
    invite = ("<p class='plantext gap'>Invite people on your server by their Gamma Cloud username; members "
              "sign in with their own Gamma Cloud account.</p>") if plan == "pro" else ""
    open_btn = (f"<div class=actions><a class='btn btn--primary btn--sm' href='{pages.esc(url)}'>Open</a></div>"
                if url and state not in ("provisioning", "stopped", "deleted") else "")
    return (f"<section class=section><h2>Your server {pill}</h2><div class=body><dl class=kv>{rows}</dl>"
            f"{invite}{open_btn}</div></section>")


def _money(sub: dict) -> str:
    if sub.get("amount_usd") is None:
        return ""
    return f"${sub['amount_usd']} a {sub['interval']}"


def _subscription(sub: dict, summary: dict) -> str:
    """The plan, its renewal or end, and Manage billing."""
    name = sub["plan"].capitalize()
    status = sub["status"]
    pill_cls = "pill--ok" if status in ("active", "trialing") else "pill--warn"
    rows = f"<dt>Plan</dt><dd>{name}{' · yearly' if sub['interval'] == 'year' else ' · monthly' if sub['interval'] else ''}</dd>"
    if _money(sub):
        rows += f"<dt>Price</dt><dd>{_money(sub)}</dd>"
    if sub["cancel_at_period_end"] and sub["period_end"]:
        rows += f"<dt>Ends</dt><dd>{pages._date(sub['period_end'])}</dd>"
    elif sub["renews_at"]:
        rows += f"<dt>Renews</dt><dd>{pages._date(sub['renews_at'])}</dd>"
    if sub["plan"] == "pro" and sub["seats"] > 1:
        rows += f"<dt>Seats</dt><dd>{sub['seats']}</dd>"
    if summary["plan_source"] == "granted":
        rows += (f"<dt>Granted</dt><dd>{pages.esc(summary['granted_plan'].capitalize())}, by the operator</dd>")
    ends = ("<p class='plantext gap'>Your plan ends with this period. Manage billing resumes it.</p>"
            if sub["cancel_at_period_end"] else "")
    return (f"<section class=section><h2>{name} <span class='pill {pill_cls}'>{pages.esc(status.replace('_', ' '))}"
            f"</span></h2><div class=body><dl class=kv>{rows}</dl>{ends}<div class=actions>"
            "<button class='btn btn--sm' data-portal>Manage billing</button></div></div></section>")


def _strip(text: str, button: str = "", bad: bool = False) -> str:
    return f"<div class='notice{' bad' if bad else ''}'><span>{text}</span>{button}</div>"


FIX = "<button class='btn btn--sm' data-portal>Fix payment</button>"


def _past_due(sub: dict) -> str:
    if sub["grace_ends_at"] and _days_left(sub["grace_ends_at"]) > 0:
        text = (f"<b>Your last payment failed.</b> Your server becomes read-only on "
                f"{pages._date(sub['grace_ends_at'])} unless a payment goes through.")
    else:
        text = "<b>Your last payment failed</b> and the grace period is over: your server is read-only until it is paid."
    return _strip(text, FIX, bad=True)


def _ended(sub: dict, summary: dict, hosted: dict | None) -> str:
    """Cancelled, or unpaid after Stripe's retries: the retention countdown,
    Export, and Resume (a new checkout) or Fix payment."""
    url = (hosted or {}).get("url") or ""
    if sub["status"] == "unpaid":
        head = "<b>Your payments failed and the retries ran out.</b> Your server is read-only; pay the open invoice to resume."
        action = FIX
    else:
        head = (f"<b>Your {pages.esc(sub['plan'].capitalize())} plan ended</b>"
                + (f" on {pages._date(sub['ended_at'])}." if sub["ended_at"] else "."))
        price = f"{sub['plan']}_{sub['interval'] or 'month'}"
        action = (f"<button class='btn btn--primary btn--sm' data-resume='{pages.esc(price)}'>Resume</button>"
                  if can_buy(summary) and sub["plan"] in ("plus", "pro") else "")
    rows = ""
    if sub["read_only_until"] and sub["status"] != "unpaid":
        left = _days_left(sub["read_only_until"])
        rows += (f"<dt>Readable until</dt><dd>{pages._date(sub['read_only_until'])}"
                 f"{f' · {left} days left' if left else ''}</dd>")
    if sub["deletes_at"] and sub["status"] != "unpaid":
        rows += f"<dt>Deleted on</dt><dd>{pages._date(sub['deletes_at'])}</dd>"
    export = (f"<a class='btn btn--sm' href='{pages.esc(url.rstrip('/'))}/?settings=backups'>Export</a>" if url else "")
    text = ("<p class=plantext>Your library stays readable for a while: export it, or resume to keep "
            "working on it. After that the server is stopped, then deleted with its files and copies.</p>")
    return (_strip(head, "", bad=True)
            + f"<section class=section><h2>What happens to your server</h2><div class=body>{text}"
            + (f"<dl class='kv gap'>{rows}</dl>" if rows else "")
            + f"<div class=actions>{export}{action}</div></div></section>")


# --- the page -----------------------------------------------------------------

STYLE = """<style>
.plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:12px}
.plancard{padding:18px;display:flex;flex-direction:column;gap:8px}
.plancard.cur{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.plancard h3{font-size:16px;display:flex;align-items:center;gap:8px}.plancard .price{font-size:26px;font-weight:600;letter-spacing:-.02em}
.plancard .price small{font-size:13px;font-weight:400;color:var(--muted);letter-spacing:0}.plancard .save{font-size:12.5px;color:var(--muted);min-height:1.3em}
.plancard ul{margin:4px 0 6px;padding-left:18px;color:var(--text-2);font-size:13.5px;flex:1}.plancard li+li{margin-top:5px}
.interval{display:inline-flex;border:1px solid var(--line-2);border-radius:8px;padding:2px;margin-bottom:14px;background:var(--surface)}
.interval button{background:none;border:0;padding:5px 12px;border-radius:6px;font:inherit;font-weight:500;color:var(--text-2);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.interval button.on{background:var(--surface-2);color:var(--text)}
.plans:not(.y) [data-y],.plans.y [data-m]{display:none}
.notice.bad{background:var(--danger-soft);color:var(--danger);border-color:color-mix(in srgb,var(--danger) 35%,transparent)}
.gap{margin-top:12px}.kv+.actions{margin-top:16px}#bmsg{margin:0 0 12px}#bmsg:empty{display:none}
@media(max-width:820px){.plans{grid-template-columns:1fr}}
</style>"""

SCRIPT = """
const bmsg = document.getElementById('bmsg');
let interval = 'month';
document.querySelectorAll('[data-interval]').forEach(b => b.onclick = () => {
  interval = b.dataset.interval;
  document.querySelectorAll('[data-interval]').forEach(x => x.classList.toggle('on', x === b));
  const p = document.getElementById('plans'); if (p) p.classList.toggle('y', interval === 'year');
});
const go = (b, path, body) => act(b, async () => { const d = await api(path, body); location.href = d.url; }, bmsg);
document.querySelectorAll('[data-choose]').forEach(b => b.onclick = () => go(b, '/api/billing/checkout', {price: b.dataset.choose + '_' + interval}));
document.querySelectorAll('[data-resume]').forEach(b => b.onclick = () => go(b, '/api/billing/checkout', {price: b.dataset.resume}));
document.querySelectorAll('[data-portal]').forEach(b => b.onclick = () => go(b, '/api/billing/portal', {}));
const setup = document.getElementById('setup');
if (setup) {
  const started = Date.now();
  const poll = async () => {
    try {
      const s = (await api('/api/hosted/status', undefined, 'GET')).server;
      if (s && s.state === 'running' && s.url) {
        document.getElementById('setuphead').textContent = 'Your server is ready';
        document.getElementById('setuptext').textContent = 'Your Gamma server runs at ' + s.url.replace(/^https?:\\/\\//, '') + '. Sign in there with this account.';
        const o = document.getElementById('open'); o.href = s.url; o.hidden = false; return;
      }
    } catch (e) { if (e.status === 401) { location.href = '/login?next=/plan'; return; } }
    if (Date.now() - started > 15 * 60 * 1000) { bmsg.textContent = 'This is taking longer than it should. Reload in a few minutes, or write to support.'; return; }
    setTimeout(poll, 3000);
  };
  poll();
}
"""


def _setup(account: dict) -> str:
    return ("<section class=section><h2 id=setuphead>Setting up your server…</h2><div class=body id=setup>"
            f"<p class=plantext id=setuptext>Thank you. Your Gamma server at <b>{pages.esc(_host(account['username']))}</b> "
            "is being created; this takes a minute or two and this page updates by itself.</p>"
            "<div class=actions><a class='btn btn--primary btn--sm' id=open href='#' hidden>Open</a>"
            "<a class='btn btn--sm' href='/plan'>Back to your plan</a></div></div></section>")


def plan_state(summary: dict, checkout: str = "") -> str:
    """``setup`` / ``active`` / ``past_due`` / ``paused`` / ``ended`` /
    ``free``. ``?checkout=success`` means "setting up" only for an account
    Stripe knows (a subscription row or a customer) whose server does not
    run yet; on any other account the parameter is ignored."""
    sub, hosted = summary["subscription"], summary["hosted"]
    known = bool(sub) or summary.get("has_customer")
    if checkout == "success" and known and not (hosted and hosted.get("state") == "running"):
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


def _paused() -> str:
    """Stripe paused the subscription (a trial ended without a payment
    method, or the operator paused collection): the portal resumes it; a
    new checkout would be refused while it is held."""
    return _strip("<b>Payment collection is paused.</b> Your plan does not renew and your server is read-only "
                  "until it resumes. Add a payment method or resume it under Manage billing.",
                  "<button class='btn btn--sm' data-portal>Manage billing</button>", bad=True)


def plan_page(account: dict, summary: dict, checkout: str = "") -> str:
    """``account``: ``accounts.public``; ``summary``: ``billing.summary``;
    ``checkout``: the ``?checkout=`` Stripe sent the browser back with."""
    account = {**account, "plan": summary["plan"]}
    state = plan_state(summary, checkout)
    sub, hosted = summary["subscription"], summary["hosted"]
    plan = summary["plan"]
    read_only = _strip("<b>Your server is read-only.</b> Writes are refused until the plan is paid again; "
                       "reading and export work.", "", bad=True) if hosted and hosted.get("read_only") and state != "ended" else ""
    if state == "setup":
        inner = _setup(account)
    elif state in ("active", "past_due", "paused"):
        thanks = _strip("<b>Thank you.</b> Your plan is active.") if checkout == "success" and state == "active" else ""
        strip = _past_due(sub) if state == "past_due" else _paused() if state == "paused" else read_only
        inner = (thanks + strip
                 + f"<div class=cols><div>{_server(hosted, sub['plan'])}</div><div>{_subscription(sub, summary)}</div></div>")
    elif state == "ended":
        # unpaid is still held: it is paid in the portal, so no plan cards to buy from
        inner = _ended(sub, summary, hosted) + (_cards(summary, account) if sub["status"] not in billing.HELD_STATUSES
                                                else "")
    else:
        granted = ""
        if summary["plan_source"] == "granted":
            granted = _strip(f"<b>{pages.esc(plan.capitalize())} plan, granted.</b> The Gamma Cloud operator gave this "
                             "account its plan; there is nothing to pay.")
        server = f"<div class=cols><div>{_server(hosted, plan)}</div><div></div></div>" if hosted else ""
        # Stripe's webhook may not have arrived yet; nothing here can tell a real return from a typed URL
        pending = (_strip("If you have just paid, your plan shows here within a minute. Reload this page.")
                   if checkout == "success" else "")
        inner = pending + granted + read_only + server + _cards(summary, account)
    lead = ("The desktop app is free. A paid plan adds a Gamma server of your own, always on, at your own address."
            if state in ("free", "ended") else "Your plan, its billing and your hosted server.")
    script = SCRIPT
    if sub:  # the page never calls Stripe; this read refreshes a stale row and reloads if it moved
        shown = {"status": sub["status"], "plan": plan}
        script += (f"const SHOWN = {pages._js(shown)};\n"
                   "api('/api/billing/me', undefined, 'GET').then(d => { if (d.plan !== SHOWN.plan || "
                   "(d.subscription && d.subscription.status) !== SHOWN.status) location.reload(); }).catch(() => {});")
    return pages.app("Plan", lead, account, "plan", STYLE + "<div class=msg id=bmsg></div>" + inner, script)


# --- the Admin page's Billing tab ---------------------------------------------

STATUSES = ("active", "trialing", "past_due", "unpaid", "canceled", "incomplete", "incomplete_expired", "paused")

ADMIN_TAB = (
    "<div id=tab-billing hidden><div class=toolbar><select id=bstatus class=sm aria-label='Status'>"
    "<option value=''>Every status</option>"
    + "".join(f"<option value={s}>{s.replace('_', ' ')}</option>" for s in STATUSES)
    + "</select><span class=spacer></span><span class=empty id=bcount></span></div>"
    "<div class=notice id=boff hidden><span>Billing is off: <code>GAMMA_CLOUD_STRIPE_SECRET</code> is not set.</span></div>"
    "<div class=section><div class='body tbl'><table><thead><tr><th>Account</th><th>Plan</th><th>Status</th>"
    "<th>Period end</th><th>Cancels</th><th>Stripe customer</th><th></th></tr></thead><tbody id=subs></tbody></table></div></div>"
    "<p class=empty>Refunds, disputes and invoices stay in Stripe's dashboard; whatever changes there comes back "
    "through the webhook. The plan select on the Accounts tab is a courtesy grant: it never ends a subscription, "
    "and the higher of the two is the plan.</p></div>")

ADMIN_JS = """
// --- Billing tab (pages_billing.py) ---
async function loadBilling(){
  const sel = document.getElementById('bstatus');
  sel.onchange = () => loadBilling();
  const d = await api('/api/admin/subscriptions?status=' + encodeURIComponent(sel.value), undefined, 'GET');
  document.getElementById('boff').hidden = d.enabled;
  document.getElementById('bcount').textContent = d.subscriptions.length + ' shown';
  document.getElementById('subs').innerHTML = d.subscriptions.map(s => '<tr><td><b>' + esc(s.username || '(deleted)') + '</b><br><span class=mono>' + esc(s.email || s.account_id) + '</span></td>'
    + '<td>' + esc(s.plan) + (s.effective_plan && s.effective_plan !== s.plan ? ' <span class=pill>' + esc(s.effective_plan) + ' effective</span>' : '') + '</td>'
    + '<td><span class="pill' + (s.status === 'active' || s.status === 'trialing' ? ' pill--ok' : ' pill--warn') + '">' + esc(s.status.replace('_', ' ')) + '</span></td>'
    + '<td>' + esc((s.current_period_end || '').slice(0, 10)) + '</td><td>' + (s.cancel_at_period_end ? 'at period end' : '') + '</td>'
    + '<td>' + (s.stripe_customer_id ? '<a class=mono href="https://dashboard.stripe.com' + (d.test_mode ? '/test' : '') + '/customers/' + encodeURIComponent(s.stripe_customer_id) + '" target=_blank rel=noopener>' + esc(s.stripe_customer_id) + '</a>' : '') + '</td>'
    + '<td><button class="btn btn--sm" data-bref="' + esc(s.account_id) + '"' + (d.enabled && s.stripe_subscription_id ? '' : ' disabled') + '>Refresh</button></td></tr>').join('')
    || '<tr><td colspan=7 class=empty>No subscriptions.</td></tr>';
  document.querySelectorAll('[data-bref]').forEach(b => b.onclick = () => act(b, async () => { await api('/api/admin/subscriptions/' + b.dataset.bref + '/refresh', {}); await loadBilling(); }));
}
"""

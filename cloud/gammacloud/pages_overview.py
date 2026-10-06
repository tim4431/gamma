"""The Admin page's Overview tab, the one open on load (docs/dev/hosted.md
"Alerts"): what needs attention, then a strip of counts (accounts, what is
paid, servers, hosts, failed jobs) and one line of the sign-up mode and the
plans on sale, all from ``GET /api/admin/overview``.

``pages.admin_page`` includes ``ADMIN_TAB`` first and appends ``ADMIN_JS``,
which defines ``loadOverview()``, the tab's entry; it uses the admin
script's ``api``, ``esc`` and ``act``. An alert's link is a hash
(``#servers``, ``#machines/<host id>``), which the admin script's tab switch
follows. The tab reloads every 60 s while it is the open tab of a visible
page, and not otherwise.
"""

ADMIN_TAB = (
    "<div id=tab-overview><div class=notice id=oerr hidden></div>"
    "<div class=section><h2>Needs attention <span id=ohead></span></h2><div class=body id=oalerts>"
    "<p class=empty>Loading…</p></div></div>"
    "<div class=tiles id=osum></div><p class=empty id=oline></p></div>")

ADMIN_JS = r"""
// --- Overview tab (pages_overview.py) ---
const loadOverview = (() => {
const TAB = document.getElementById('tab-overview'), $ = id => document.getElementById(id);
const LINKS = {'#machines': 'Machines', '#servers': 'Servers', '#billing': 'Billing', '#settings': 'Settings'};   // by the tab, before any '/'
const ORDER = ['running', 'provisioning', 'grace', 'read_only', 'suspended', 'stopped'];
let timer = 0, gen = 0;

const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
const warn = (n, word) => n ? ' · <span class="pill pill--warn">' + n + ' ' + word + '</span>' : '';
const tile = (label, value, detail) => '<div><span>' + label + '</span><b>' + value + '</b><small>' + detail + '</small></div>';
function lasted(ts){
  const s = (Date.now() - Date.parse(ts)) / 1000;
  if (isNaN(s)) return '';
  return s < 90 ? 'a moment' : s < 5400 ? Math.round(s / 60) + ' min' : s < 129600 ? Math.round(s / 3600) + ' h' : Math.round(s / 86400) + ' d';
}
function alertRow(a){
  const tab = LINKS[String(a.link).split('/')[0]];
  return '<div class=row><div><b>' + esc(a.text) + '</b><span class=sub><span title="' + esc('since ' + new Date(a.first_at).toLocaleString()) + '">for '
    + lasted(a.first_at) + '</span>' + (a.mailed_at ? ' · mailed' : '') + (tab ? ' · <a href="' + esc(a.link) + '">' + tab + '</a>' : '')
    + '</span></div><button type=button class="btn btn--sm" data-odismiss="' + esc(a.key) + '">Dismiss</button></div>';
}
function render(d){
  const al = d.alerts || [], st = d.settings, a = d.accounts, b = d.billing, f = d.fleet, by = f.servers.by_state;
  $('ohead').textContent = (al.length ? plural(al.length, 'problem', 'problems') + ' · ' : '')
    + (st.alerts ? 'mailed to ' + (st.alert_email || 'every admin') : 'alerts are off: nothing is mailed');
  $('oalerts').innerHTML = al.map(alertRow).join('') || '<p class=empty>Nothing needs attention.</p>';
  const servers = ORDER.reduce((n, k) => n + (by[k] || 0), 0), hosts = f.hosts.fresh + f.hosts.stale;
  $('osum').innerHTML = tile('Accounts', a.total, a.new_7d + ' new this week · ' + a.unverified + ' unverified' + (a.deleted ? ' · ' + a.deleted + ' deleted' : ''))
    + tile('Paying', b.paying, b.enabled ? Object.entries(b.by_plan).map(([p, n]) => n + ' ' + esc(p)).join(' · ') + warn(b.past_due, 'payment failed') : 'billing is off')
    + tile('A month', '$' + Number(b.mrr_usd || 0).toFixed(2), b.ending ? b.ending + ' set to end' : 'a yearly plan counts a twelfth')
    + tile('Servers', servers, (ORDER.filter(k => by[k]).map(k => by[k] + ' ' + k.replace(/_/g, '-')).join(' · ') || 'none yet') + warn(f.servers.outdated, 'outdated'))
    + tile('Hosts', hosts, hosts ? f.hosts.fresh + ' fresh' + warn(f.hosts.stale, 'stale') : 'none yet')
    + tile('Failed jobs', f.jobs.failed, f.jobs.queued + ' queued · ' + f.jobs.running + ' running');
  const sale = st.plans_on_sale.map(p => p.charAt(0).toUpperCase() + p.slice(1));
  $('oline').innerHTML = 'Registration: ' + esc(st.registration) + ' · anti-bot check ' + (st.turnstile_on ? 'on' : 'off')
    + ' · on sale: ' + esc(sale.join(', ') || 'nothing') + ' · <a href="#settings">Settings</a>';
}

// --- loading, and the 60 s reload while the tab is in view ---
const shown = () => !document.hidden && !TAB.hidden;
function schedule(){ clearTimeout(timer); timer = shown() ? setTimeout(() => { if (shown()) refresh(); }, 60000) : 0; }
async function refresh(){
  clearTimeout(timer); timer = 0;
  const seq = ++gen, err = $('oerr');
  let d;
  try { d = await api('/api/admin/overview', undefined, 'GET'); }
  catch (e) {
    if (e.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
    if (seq === gen) { err.hidden = false; err.textContent = 'Could not load the overview: ' + e.message; schedule(); }
    return;
  }
  if (seq !== gen) return;
  err.hidden = true; render(d); schedule();
}
document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(timer); timer = 0; } else if (shown()) refresh(); });
TAB.addEventListener('click', (ev) => {
  const b = ev.target.closest('button[data-odismiss]'); if (!b) return;
  act(b, async () => { await api('/api/admin/alerts/' + encodeURIComponent(b.dataset.odismiss) + '/dismiss', {}); await refresh(); });
});
return refresh;
})();
"""

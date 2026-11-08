"""The Admin page's Reports tab (docs/dev/cloud.md "Reports"): the reports
Gamma servers forwarded, newest first, open ones by default, from ``GET
/api/admin/reports``. A row says what was reported (a comment, a page, an
AI reply) and why, the reporter's words, the text reported with who wrote
it and the page it is on, the server, and who sent it: the reporter's Gamma
Cloud account, the server's sign-in client, or neither (the server's word
alone). *Resolve* and *Reopen* move it between the lists, *Delete* removes
it; an open report is an alert on the Overview until it is resolved.

``pages.admin_page`` includes ``ADMIN_TAB`` and appends ``ADMIN_JS``, which
defines ``loadReports()``, the tab's entry, with the admin script's ``api``,
``esc``, ``act`` and ``ask``.
"""

import json

from .reports import KIND_LABEL, REASON_LABEL

ADMIN_TAB = (
    "<style>#tab-reports td{vertical-align:top}#tab-reports .sub{display:block;color:var(--muted);font-size:12px}"
    "#tab-reports .rtext{white-space:pre-wrap;overflow-wrap:anywhere;max-width:420px;max-height:9em;overflow:auto;"
    "margin-top:4px}</style>"
    "<div id=tab-reports hidden><div class=toolbar><select id=rstate class=sm>"
    "<option value=open>Open</option><option value=resolved>Resolved</option><option value=all>All</option></select>"
    "<span class=spacer></span><span class=empty id=rcount></span></div>"
    "<div class=section><div class='body tbl'><table><thead><tr><th>When</th><th>What</th><th>Reported</th>"
    "<th>Server</th><th>Sent by</th><th></th></tr></thead><tbody id=reports></tbody></table></div></div></div>")


def admin_js() -> str:
    labels = json.dumps({"kind": KIND_LABEL, "reason": REASON_LABEL})
    return r"""
// --- Reports tab (pages_reports.py) ---
const loadReports = (() => {
const L = """ + labels + r""", $ = id => document.getElementById(id);
const when = ts => esc(String(ts || '').slice(0, 16).replace('T', ' '));
function sender(r){
  if (r.account_id) return '<b>' + esc(r.account_username || r.account_id) + '</b><span class=sub>Gamma Cloud account</span>';
  if (r.client_id) return '<span class=mono>' + esc(r.client_id) + '</span><span class=sub>the server\'s sign-in client</span>';
  return '<span class=sub>not signed: the server\'s word</span>';
}
function row(r){
  const what = (L.kind[r.kind] || r.kind) + ': ' + (L.reason[r.reason] || r.reason);
  const on = [r.author ? 'by ' + r.author : '', r.title ? 'on “' + r.title + '”' : ''].filter(Boolean).join(' ');
  const link = r.link ? ' · <a href="' + esc(r.link) + '" target=_blank rel="noopener noreferrer">open</a>' : '';
  const reporter = r.reporter ? 'Reporter on the server: ' + esc(r.reporter) : 'Reporter on the server: a visitor without an account';
  const state = r.state === 'resolved' ? '<span class=sub>resolved ' + when(r.resolved_at) + '</span>' : '';
  return '<tr data-report="' + esc(r.id) + '"><td class=mono>' + when(r.created_at) + state + '</td>'
    + '<td><b>' + esc(what) + '</b>' + (r.details ? '<div class=rtext>' + esc(r.details) + '</div>' : '') + '</td>'
    + '<td>' + (on ? '<span class=sub>' + esc(on) + link + '</span>' : link ? '<span class=sub>' + link.slice(3) + '</span>' : '')
    + (r.content ? '<div class=rtext>' + esc(r.content) + '</div>' : '') + '</td>'
    + '<td>' + esc(r.server_name || r.server_url || '—') + (r.server_url && r.server_name !== r.server_url ? '<span class="sub mono">' + esc(r.server_url) + '</span>' : '') + '</td>'
    + '<td>' + sender(r) + '<span class=sub>' + reporter + '</span></td>'
    + '<td class=acts><button class="btn btn--sm" data-rstate="' + (r.state === 'open' ? 'resolved' : 'open') + '">' + (r.state === 'open' ? 'Resolve' : 'Reopen')
    + '</button> <button class="btn btn--sm" data-rdel>Delete</button></td></tr>';
}
async function load(){
  const state = $('rstate').value, d = await api('/api/admin/reports?state=' + state, undefined, 'GET');
  $('rcount').textContent = d.open === 1 ? '1 open' : d.open + ' open';
  $('reports').innerHTML = d.reports.map(row).join('') || '<tr><td colspan=6 class=empty>' + (state === 'open' ? 'No open reports.' : 'No reports.') + '</td></tr>';
}
$('rstate').onchange = () => load();
$('reports').addEventListener('click', (ev) => {
  const b = ev.target.closest('button'), tr = b && b.closest('tr[data-report]'); if (!tr) return;
  const id = encodeURIComponent(tr.dataset.report);
  if (b.dataset.rstate) act(b, async () => { await api('/api/admin/reports/' + id, {state: b.dataset.rstate}, 'PATCH'); await load(); });
  else if ('rdel' in b.dataset) (async () => {
    if (!await ask('Delete this report? It cannot be brought back.', {ok: 'Delete', danger: true})) return;
    act(b, async () => { await api('/api/admin/reports/' + id, undefined, 'DELETE'); await load(); });
  })();
});
return load;
})();
"""

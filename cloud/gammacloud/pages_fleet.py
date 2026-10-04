"""The Admin page's Servers tab: hosts, hosted servers, an upgrade run in
waves, and the job queue (docs/dev/hosted.md). ``pages.admin_page``
includes ``ADMIN_TAB`` and appends ``ADMIN_JS``, which defines
``loadServers()``; it uses the admin script's ``api``, ``esc``, ``bind``,
``say`` and ``act``."""

import json

from .hosted import ACTIONS

_ACTIONS = "".join(f"<option value={a}>{a.capitalize()}</option>" for a in ACTIONS)

ADMIN_TAB = (
    "<div id=tab-servers hidden>"
    "<div class=section><h2>Hosts <span>the machines a fleet agent runs on</span></h2><div class=body>"
    "<form id=fhost class=inline><label>Name<input name=name required placeholder='vps-1'></label>"
    "<label>Address<input name=address placeholder='optional, for your notes'></label>"
    "<button type=submit class='btn btn--primary btn--sm'>Add host</button><div class=msg></div></form>"
    "<div id=hosttoken hidden class=secretbox></div></div></div>"
    "<div class=section><div class='body tbl'><table><thead><tr><th>Host</th><th>Seen</th><th>Memory used</th>"
    "<th>Disk used</th><th>Servers</th><th>Placement</th></tr></thead><tbody id=hosts></tbody></table></div></div>"
    "<div class=section><h2>Hosted servers <span>one per account on a hosted plan</span></h2><div class=body>"
    "<form id=fprov class=inline><label>Account id<input name=account_id required autocomplete=off></label>"
    "<button type=submit class='btn btn--sm'>Provision</button><div class=msg></div></form></div></div>"
    "<div class=section><div class='body tbl'><table><thead><tr><th>Server</th><th>Account</th><th>Plan</th>"
    "<th>State</th><th>Version</th><th>Data</th><th>Last report</th><th></th></tr></thead><tbody id=servers></tbody>"
    "</table></div></div>"
    "<div class=section><h2>Upgrade <span>every running server to an image tag, a wave at a time</span></h2>"
    "<div class=body><form id=fupg class=inline><label>Image tag<input name=tag required placeholder='sha-abc1234'>"
    "</label><label>Wave size<input name=wave_size type=number value=1 min=1 max=100></label>"
    "<button type=submit class='btn btn--sm'>Upgrade</button><div class=msg></div></form></div></div>"
    "<div class=section><h2>Jobs <span>the last 100</span></h2><div class='body tbl'><table><thead><tr><th>Created</th>"
    "<th>Job</th><th>Server</th><th>Host</th><th>State</th><th>Result</th><th></th></tr></thead><tbody id=jobs></tbody>"
    "</table></div></div>"
    "</div>")

ADMIN_JS = """
const SRV_PILL = {running: 'pill pill--ok', grace: 'pill pill--warn', read_only: 'pill pill--warn',
  stopped: 'pill pill--warn', suspended: 'pill pill--warn'};
const JOB_PILL = {done: 'pill pill--ok', failed: 'pill pill--warn', running: 'pill', queued: 'pill', held: 'pill', canceled: 'pill'};
function when(ts){ return ts ? esc(ts.slice(0, 16).replace('T', ' ')) : '<span class=empty>never</span>'; }
function size(mb){ mb = Number(mb) || 0; return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB'; }
function used(a, b){ return b ? size(a) + ' / ' + size(b) : '<span class=empty>no report</span>'; }
function hostRow(h){
  return '<tr><td><b>' + esc(h.name) + '</b><br><span class=mono>' + esc(h.id) + '</span>' + (h.address ? ' ' + esc(h.address) : '')
    + (h.agent_version ? '<br><span class=empty>agent ' + esc(h.agent_version) + '</span>' : '') + '</td>'
    + '<td>' + when(h.last_seen_at) + (h.stale ? ' <span class="pill pill--warn">stale</span>' : '') + '</td>'
    + '<td>' + used(h.memory_used_mb, h.memory_mb) + '</td><td>' + used(h.disk_used_mb, h.disk_mb) + '</td><td>' + h.servers + '</td>'
    + '<td>' + (h.accepting ? '<span class="pill pill--ok">accepting</span>' : '<span class=pill>closed</span>')
    + ' <button class="btn btn--sm" data-hostacc="' + esc(h.id) + '" data-on="' + (h.accepting ? 0 : 1) + '">' + (h.accepting ? 'Close' : 'Open') + '</button></td></tr>';
}
function serverRow(s){
  const r = s.report || {}, ag = r.agent || {}, jobs = s.jobs || {};
  const data = r.data_bytes ? size(Math.round(r.data_bytes / 1048576)) : (ag.data_mb ? size(ag.data_mb) : '');
  const label = s.url && s.state !== 'deleted' ? '<a href="' + esc(s.url) + '" target=_blank rel=noopener><b>' + esc(s.label) + '</b></a>' : '<b>' + esc(s.label) + '</b>';
  const state = '<span class="' + (SRV_PILL[s.state] || 'pill') + '">' + esc(s.state.replace('_', '-')) + '</span>'
    + (s.read_only ? ' <span class="pill pill--warn">read-only</span>' : '')
    + (ag.last_seen_at ? ' <span class="pill' + (ag.running ? ' pill--ok' : ' pill--warn') + '">' + (ag.running ? 'up' : 'down') + '</span>' : '')
    + (r.note ? '<br><span class=empty>' + esc(r.note) + '</span>' : '')
    + (jobs.failed ? '<br><span class=empty>' + jobs.failed + ' failed job(s)</span>' : '');
  const acts = s.state === 'deleted' ? '' : '<select class=sm data-srv="' + esc(s.id) + '"><option value="">Actions…</option>' + %s + '</select>';
  return '<tr><td>' + label + '<br><span class=mono>' + esc(s.id) + '</span>' + (s.host ? '<br><span class=empty>on ' + esc(s.host) + '</span>' : '') + '</td>'
    + '<td>' + esc(s.username) + '<br><span class=mono>' + esc(s.account_id) + '</span></td><td>' + esc((s.limits || {}).plan || s.plan) + '</td>'
    + '<td>' + state + '</td><td>' + esc(r.version || ag.image || '') + '</td><td>' + data + '</td><td>' + when(s.synced_at) + '</td><td>' + acts + '</td></tr>';
}
function jobRow(j){
  let result = j.result || ''; try { const o = JSON.parse(result); result = o.error || o.message || (o && typeof o === 'object' ? Object.entries(o).map(([k, v]) => k + ': ' + v).join(', ') : result); } catch (e) {}
  const btns = (['failed', 'canceled'].includes(j.state) ? '<button class="btn btn--sm" data-job="' + esc(j.id) + '" data-do=retry>Retry</button> ' : '')
    + (['queued', 'held', 'failed'].includes(j.state) ? '<button class="btn btn--sm" data-job="' + esc(j.id) + '" data-do=cancel>Cancel</button>' : '');
  return '<tr><td class=mono>' + esc(j.created_at.slice(0, 19).replace('T', ' ')) + '</td><td>' + esc(j.kind) + (j.wave ? '<br><span class=mono>' + esc(j.wave) + '</span>' : '')
    + '</td><td>' + esc(j.label || j.server_id) + '</td><td>' + esc(j.host || j.host_id) + '</td><td><span class="' + (JOB_PILL[j.state] || 'pill') + '">' + esc(j.state) + '</span></td>'
    + '<td>' + esc(String(result).slice(0, 200)) + '</td><td>' + btns + '</td></tr>';
}
async function loadServers(){
  const [h, s, j] = await Promise.all([api('/api/admin/hosts', undefined, 'GET'), api('/api/admin/servers', undefined, 'GET'),
    api('/api/admin/jobs?limit=100', undefined, 'GET')]);
  document.getElementById('hosts').innerHTML = h.hosts.map(hostRow).join('') || '<tr><td colspan=6 class=empty>No hosts. Add one, then start its agent with the token.</td></tr>';
  document.getElementById('servers').innerHTML = s.servers.map(serverRow).join('') || '<tr><td colspan=8 class=empty>No hosted servers.</td></tr>';
  document.getElementById('jobs').innerHTML = j.jobs.map(jobRow).join('') || '<tr><td colspan=7 class=empty>No jobs.</td></tr>';
  document.querySelectorAll('[data-hostacc]').forEach(b => b.onclick = () => act(b, async () => {
    await api('/api/admin/hosts/' + b.dataset.hostacc, {accepting: b.dataset.on === '1'}, 'PATCH'); loadServers(); }));
  document.querySelectorAll('[data-srv]').forEach(sel => sel.onchange = async () => {
    const id = sel.dataset.srv, v = sel.value; sel.value = ''; if (!v) return;
    if (v === 'delete' && !confirm('Delete this server now? Its container, data and off-site copies are removed. This cannot be undone.')) return;
    if (v === 'suspend' && !confirm('Suspend this server? It turns read-only until you resume it.')) return;
    if (v === 'stop' && !confirm('Stop this container? The lifecycle state stays; start it again from here.')) return;
    try { await api('/api/admin/servers/' + id + '/' + v, {}); loadServers(); } catch (e) { alert(e.message); }
  });
  document.querySelectorAll('[data-job]').forEach(b => b.onclick = () => act(b, async () => {
    await api('/api/admin/jobs/' + b.dataset.job + '/' + b.dataset.do, {}); loadServers(); }));
}
bind('fhost', async (d, msg) => { const r = await api('/api/admin/hosts', {name: d.name, address: d.address});
  const box = document.getElementById('hosttoken'); box.hidden = false;
  box.textContent = 'GAMMA_FLEET_HOST_TOKEN=' + r.token + '   (shown once)'; say(msg, 'Host ' + r.host.name + ' added.'); loadServers(); });
bind('fprov', async (d, msg) => { const r = await api('/api/admin/servers/provision', {account_id: d.account_id.trim()});
  say(msg, r.server.label + ': ' + r.server.state.replace('_', '-') + '.'); loadServers(); });
bind('fupg', async (d, msg) => { if (!confirm('Upgrade every running server to ' + d.tag + '?')) return;
  const r = await api('/api/admin/servers/upgrade', {tag: d.tag, wave_size: Number(d.wave_size) || 1});
  say(msg, r.jobs + ' server(s) in ' + r.waves + ' wave(s), run ' + r.run + '.'); loadServers(); });
""" % json.dumps(_ACTIONS)

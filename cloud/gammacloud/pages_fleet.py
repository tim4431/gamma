"""The Admin page's Servers tab (docs/dev/hosted.md "Admin"): a summary
strip, the hosts with their capacity, public IP and orphan containers,
the hosted servers with their actions and DNS state, a log viewer, a server's history and its own
limits, the extra environment (every server's, or one server's), the
default image tag with automatic upgrades and upgrade runs in waves, and
the job queue. Hosts and servers carry the last 48 hours of a few series
(``trend``), drawn as sparklines in their cells.
``pages.admin_page`` includes ``ADMIN_TAB`` and appends
``ADMIN_JS``, which defines ``loadServers()``, the tab's entry; it uses the
admin script's ``api``, ``esc``, ``bind``, ``say``, ``act``, ``ask`` and
``toast``.

The tables are re-rendered every 4 s while a job is queued or running and
the tab is in view, and not at all otherwise. Clicks and selects are
delegated to the tab through ``data-f`` attributes, which the other tabs'
``wire()`` never selects. The ``<style>`` block below holds only what the
portal's stylesheet has no class for: the capacity meters, the log
viewer, the sparklines and history charts, and the notes under a form."""

import json

from . import config

JOB_STATES = ("queued", "held", "running", "done", "failed", "canceled")
# plan -> quota_mb, for the hosted plans: the quota a server shows before its first sync
_QUOTAS = {plan: lim["quota_mb"] for plan, lim in config.PLAN_LIMITS.items() if lim.get("hosted")}

STYLE = """<style>
#tab-servers .sub{display:block;color:var(--muted);font-size:12px;margin-top:1px}
#tab-servers .tbl{overflow-x:auto}#tab-servers th{white-space:nowrap}#tab-servers td{vertical-align:top}#tab-servers td.nw,#tab-servers td.nw .sub{white-space:nowrap}#tab-servers td.fres{max-width:260px;overflow-wrap:anywhere}
#tab-servers tr.fgrp td{border-bottom:0;padding-bottom:2px}#tab-servers tr.fgone td{opacity:.6}
#tab-servers .forph{display:inline-flex;align-items:center;gap:6px;margin:3px 12px 0 0}
#tab-servers .cap{display:flex;width:100%;max-width:190px;height:5px;margin:5px 0 3px;border-radius:99px;background:var(--accent-soft);overflow:hidden}
#tab-servers .cap b{display:block;background:var(--accent)}#tab-servers .cap i{display:block;background:color-mix(in srgb,var(--accent) 40%,var(--accent-soft))}
#tab-servers .cap b+i{border-left:2px solid var(--surface)}#tab-servers .cap.hot b{background:var(--danger)}
#tab-servers .section>.body+.body{border-top:1px solid var(--line)}
#tab-servers #flognote{margin:0}#tab-servers .fnote{margin:10px 0 0;color:var(--muted);font-size:12.5px;line-height:1.5}
#tab-servers .flog{margin:0;max-height:440px;overflow:auto;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;font:12px/1.5 var(--mono);color:var(--text);white-space:pre-wrap;word-break:break-all}
#tab-servers .fspark{display:inline-block;width:60px;height:16px;margin-left:6px;vertical-align:-3px}#tab-servers .sub.fnw{white-space:nowrap}
#tab-servers .fcharts{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}#tab-servers .fcharts b{font-weight:500}
#tab-servers .fchart{display:block;width:100%;height:60px;margin-top:6px}
</style>"""

ADMIN_TAB = (
    "<div id=tab-servers hidden>" + STYLE
    + "<div class=notice id=ferr hidden></div><div class=tiles id=fsum></div>"
    # hosts
    "<div class=section><h2>Hosts <span>the machines a fleet agent runs on</span></h2>"
    "<div class='body tbl'><table><thead><tr><th>Host</th><th>Heartbeat</th><th>Memory</th><th>Disk</th>"
    "<th>Servers</th><th>Placement</th></tr></thead><tbody id=fhosts></tbody></table></div>"
    "<div class=body><form id=fhost class=inline autocomplete=off><label>Name<input name=name required placeholder='vps-1'></label>"
    "<label>Address<input name=address placeholder='optional, for your notes'></label>"
    "<label>Public IP<input name=public_ip placeholder='blank: behind this Caddy' spellcheck=false></label>"
    "<button type=submit class='btn btn--primary btn--sm'>Add host</button><div class=msg></div></form>"
    "<p class=fnote>A host with a public IP runs a Caddy of its own, and each server on it gets a DNS record of "
    "its own at Cloudflare, so it takes servers only while the Cloudflare token is set. Leave it blank for the "
    "host that runs beside the account server's Caddy.</p>"
    "<div id=fhosttoken hidden class=secretbox></div></div></div>"
    # hosted servers
    "<div class=section><h2>Hosted servers <span id=fsrvhead>one per account on a hosted plan</span></h2>"
    "<div class='body tbl'><table><thead><tr><th>Server</th><th>Account</th><th>Plan</th><th>State</th>"
    "<th>Version</th><th>Data</th><th>Last report</th><th></th></tr></thead><tbody id=fservers></tbody></table></div>"
    "<div class=body><form id=fprov class=inline autocomplete=off>"
    "<label>Account<input id=fwho list=fwholist placeholder='search a username or e-mail'></label><datalist id=fwholist></datalist>"
    "<label>Account id<input name=account_id id=fwhoid required></label>"
    "<button type=submit class='btn btn--sm'>Provision</button><div class=msg></div></form></div></div>"
    # the log viewer, opened by a server's Logs
    "<div class=section id=flogs hidden><h2>Logs <span id=flogwho></span></h2><div class=body>"
    "<div class=toolbar><span class=empty id=flogstate></span><span class=spacer></span>"
    "<button type=button class='btn btn--sm' data-f=logagain>Fetch again</button>"
    "<button type=button class='btn btn--sm' data-f=logclose>Close</button></div>"
    "<div class=notice id=flognote hidden></div><pre class=flog id=flogtext hidden></pre></div></div>"
    # a server's history, opened by its History
    "<div class=section id=fhist hidden><h2>History <span id=fhistwho></span></h2><div class=body>"
    "<div class=toolbar><select id=fhistdays class=sm aria-label='Period'><option value=168>7 days</option>"
    "<option value=720>30 days</option></select><span class=empty id=fhiststate></span><span class=spacer></span>"
    "<button type=button class='btn btn--sm' data-f=histclose>Close</button></div>"
    "<div class=fcharts id=fhistcharts></div></div></div>"
    # a server's own limits, opened by its Limits…
    "<div class=section id=flim hidden><h2>Limits <span id=flimwho></span></h2><div class=body>"
    "<form id=flimform class=inline autocomplete=off>"
    "<label>Storage, MB<input name=quota_mb type=number min=1 step=1></label>"
    "<label>Per file, MB<input name=max_upload_mb type=number min=1 step=1></label>"
    "<label>Accounts<input name=max_accounts type=number min=1 step=1></label>"
    "<label>Memory, MB<input name=memory_mb type=number min=1 step=1></label>"
    "<label>CPUs<input name=cpus type=number min=0.25 max=64 step=0.25></label>"
    "<button type=submit class='btn btn--primary btn--sm'>Save</button>"
    "<button type=button class='btn btn--sm' data-f=limplan>Back to the plan's</button>"
    "<button type=button class='btn btn--sm' data-f=limclose>Close</button><div class=msg></div></form>"
    "<p class=fnote>An empty field is the plan's number, shown in grey. The server keeps its own numbers through a "
    "plan change and a lapse. Memory and CPUs resize the container now, without a restart; the rest reach it at "
    "its next hourly sync.</p></div></div>"
    # the extra environment: every server's, or one server's (Actions → Environment…)
    "<div class=section id=fenv><h2>Environment <span id=fenvwho></span></h2>"
    "<div class='body tbl'><table><thead><tr><th>Variable</th><th>For</th><th></th></tr></thead>"
    "<tbody id=fenvlist></tbody></table></div>"
    "<div class=body><form id=fenvform class=inline autocomplete=off>"
    "<label>Name<input name=name required placeholder='SMTP_HOST' spellcheck=false></label>"
    "<label>Value<input name=value spellcheck=false></label>"
    "<button type=submit class='btn btn--sm'>Save</button><div class=msg></div></form>"
    "<p class=fnote id=fenvnote></p></div>"
    "<div class=body id=fenvall><form id=fenvrun class=inline autocomplete=off>"
    "<label>Wave size<input name=wave_size type=number value=1 min=1 max=100></label>"
    "<button type=submit class='btn btn--sm'>Apply to every server</button><div class=msg></div></form></div></div>"
    # the default image tag, and upgrade runs
    "<div class=section><h2>Upgrade <span>what new servers run, and moving the running ones, a wave at a time</span></h2>"
    "<div class=body><form id=fdef class=inline autocomplete=off><label>Default image tag<input name=tag "
    "spellcheck=false></label><label>Automatic upgrades<select name=auto><option value=off>off</option>"
    "<option value=on>on</option></select></label>"
    "<button type=submit class='btn btn--sm'>Save</button><div class=msg></div></form><p class=fnote id=fdefnote></p></div>"
    "<div class=body><form id=fupg class=inline autocomplete=off><label>Image tag<input name=tag required placeholder='sha-abc1234'>"
    "</label><label>Wave size<input name=wave_size type=number value=1 min=1 max=100></label>"
    "<button type=submit class='btn btn--sm'>Upgrade</button>"
    "<button type=button class='btn btn--sm' data-f=upgold disabled>Upgrade all outdated (0)</button>"
    "<div class=msg></div></form></div></div>"
    # the job queue
    "<div class=section><h2>Jobs <span>the last 100<select id=fjstate class=sm aria-label='State'>"
    "<option value=''>every state</option>"
    + "".join(f"<option value={s}>{s}</option>" for s in JOB_STATES)
    + "</select></span></h2><div class='body tbl'><table><thead><tr><th>Created</th><th>Job</th><th>Server</th>"
    "<th>Host</th><th>State</th><th>Took</th><th>Result</th><th></th></tr></thead><tbody id=fjobs></tbody></table></div></div>"
    "</div>")

ADMIN_JS = r"""
// --- Servers tab (pages_fleet.py) ---
const loadServers = (() => {
const QUOTAS = __QUOTAS__;   // the hosted plans, for the Provision form's hint
const TAB = document.getElementById('tab-servers'), $ = id => document.getElementById(id);
const SRV_PILL = {running: 'pill pill--ok', grace: 'pill pill--warn', read_only: 'pill pill--warn', stopped: 'pill pill--warn', suspended: 'pill pill--warn'};
const JOB_PILL = {done: 'pill pill--ok', failed: 'pill pill--warn'};
const ORDER = ['running', 'provisioning', 'grace', 'read_only', 'suspended', 'stopped'];
const UPGRADABLE = ['running', 'grace', 'read_only', 'suspended'];   // fleet.UPGRADABLE: the states a run takes
const LIMITS = ['quota_mb', 'max_upload_mb', 'max_accounts', 'memory_mb', 'cpus'];   // hosted.OVERRIDES
const KIND_WORD = {update: 'environment'};
let timer = 0, live = false, gen = 0, showDeleted = false, defImage = '', defTag = '', servers = [], autoOn = false;
let logJob = '', logFor = null, logTimer = 0, limFor = null, envFor = null, fleetEnv = [];

const words = s => String(s || '').replace(/_/g, '-');
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
function size(mb){ mb = Math.max(0, Number(mb) || 0); return mb >= 1024 ? (mb / 1024).toFixed(1).replace(/\.0$/, '') + ' GB' : Math.round(mb) + ' MB'; }
function ago(ts){
  if (!ts) return '<span class=empty>never</span>';
  const d = new Date(ts), s = (Date.now() - d.getTime()) / 1000;
  if (isNaN(s)) return esc(ts);
  const t = s < 45 ? 'just now' : s < 5400 ? Math.max(1, Math.round(s / 60)) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago';
  return '<span title="' + esc(d.toLocaleString()) + '">' + t + '</span>';
}
function took(j){
  let s = j.duration_s;
  if (s == null && j.started_at) s = ((j.finished_at ? Date.parse(j.finished_at) : Date.now()) - Date.parse(j.started_at)) / 1000;
  if (s == null || isNaN(s)) return '';
  s = Math.max(0, Math.round(s));
  const t = s < 60 ? s + ' s' : s < 3600 ? Math.floor(s / 60) + ' min ' + (s % 60) + ' s' : Math.floor(s / 3600) + ' h ' + Math.floor(s % 3600 / 60) + ' min';
  return j.state === 'running' ? t + '…' : t;
}
// A thin meter of total: a (the accent) and b (a lighter step of it) side by side; red from 90 %.
function meter(total, a, b, title){
  const pc = v => total > 0 ? Math.max(0, Math.min(100, v / total * 100)) : 0, pa = pc(a), pb = Math.min(100 - pa, pc(b));
  return '<span class="cap' + (pa + pb >= 90 ? ' hot' : '') + '" title="' + esc(title) + '"><b style="width:' + pa.toFixed(1) + '%"></b>'
    + (pb > 0 ? '<i style="width:' + pb.toFixed(1) + '%"></i>' : '') + '</span>';
}
// The hourly samples (metrics.py) of key as a line in a w by h box over the times t0 to t1: the values in the accent,
// from 0 (the baseline, muted) to their highest; title is its tooltip. '' when there is no sample.
function line(points, key, t0, t1, w, h, cls, title){
  const p = (points || []).filter(x => x[key] != null && Date.parse(x.at) >= t0);
  if (!p.length) return '';
  const top = Math.max(...p.map(x => x[key])) || 1;
  const xy = x => ((Date.parse(x.at) - t0) / (t1 - t0) * w).toFixed(1) + ',' + (h - 1 - x[key] / top * (h - 2)).toFixed(1);
  return '<svg class=' + cls + ' viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio=none><title>' + esc(title) + '</title>'
    + '<line x1=0 y1=' + (h - 0.5) + ' x2=' + w + ' y2=' + (h - 0.5) + ' stroke="var(--muted)" vector-effect=non-scaling-stroke />'
    + '<polyline points="' + p.map(xy).join(' ') + '" fill=none stroke="var(--accent)" stroke-width=1.5 vector-effect=non-scaling-stroke /></svg>';
}
// a host's or server's sparkline: the last 48 hours of key from its trend
function spark(row, key, fmt, what){
  const t1 = Date.now(), last = (row.trend || []).filter(x => x[key] != null).pop();
  return last ? line(row.trend, key, t1 - 48 * 3600e3, t1, 60, 16, 'fspark', what + ' over 48 hours, now ' + fmt(last[key])) : '';
}
const pct = v => v + ' %';
function tagOf(image){ image = String(image || ''); const i = image.lastIndexOf(':'); return i > image.lastIndexOf('/') ? image.slice(i + 1) : image; }
const tagRun = s => s.image_tag || tagOf(s.image);
function outdated(s){ return s.state !== 'deleted' && !!s.outdated; }
function resultText(j){
  let r = j.result;
  if (typeof r === 'string') { if (!r) return ''; try { r = JSON.parse(r); } catch (e) { return r; } }
  if (r == null) return '';
  if (typeof r !== 'object') return String(r);
  const count = Array.isArray(r.lines) ? r.lines.length : r.line_count;
  if (count != null) return plural(Number(count) || 0, 'log line', 'log lines') + (r.container ? ' of ' + r.container : '');
  if (r.error || r.message) return String(r.error || r.message);
  return Object.entries(r).map(([k, v]) => k + ': ' + (v && typeof v === 'object' ? JSON.stringify(v) : v)).join(', ');
}

// --- hosts ---
function memory(h){
  const total = Number(h.memory_mb) || 0, used = Number(h.memory_used_mb) || 0;
  if (!total) return '<span class=empty>no heartbeat yet</span>';
  const com = Number(h.committed_mb) || 0, res = Number(h.reserve_mb) || 0, free = Number(h.free_mb) || 0;
  return size(com) + ' / ' + size(total) + ' committed'
    + meter(total, com, res, size(com) + ' committed to servers, ' + size(res) + ' kept for the host, ' + size(free) + ' free for new servers')
    + '<span class=sub>' + (free > 0 ? size(free) + ' free' : 'full') + ' · ' + size(res) + ' reserve · ' + size(used) + ' in use'
    + spark(h, 'memory_used_mb', size, 'memory in use') + '</span>';
}
function disk(h){
  const total = Number(h.disk_mb) || 0, used = Number(h.disk_used_mb) || 0;
  if (!total) return '<span class=empty>no heartbeat yet</span>';
  return size(used) + ' / ' + size(total) + spark(h, 'disk_used_mb', size, 'disk used') + meter(total, used, 0, size(used) + ' of ' + size(total) + ' used')
    + '<span class=sub>' + size(total - used) + ' free</span>';
}
// a routed host (its own Caddy and a DNS record per server): its address, and whether placement may use it
function route(h){
  if (!h.public_ip) return '';
  const pill = h.dns === 'on' ? '<span class="pill pill--ok" title="its own Caddy; each server on it gets a DNS record of its own">dns on</span>'
    : '<span class="pill pill--warn" title="GAMMA_CLOUD_CF_API_TOKEN and _ZONE_ID are not set: placement skips this host">no dns token: closed</span>';
  return '<span class=sub><span class=mono>' + esc(h.public_ip) + '</span> ' + pill + '</span>';
}
function hostRow(h){
  const orphans = h.orphans || [], id = esc(h.id);
  return '<tr' + (orphans.length ? ' class=fgrp' : '') + '><td><b>' + esc(h.name) + '</b>' + (h.agent_version ? ' <span class=empty>agent ' + esc(h.agent_version) + '</span>' : '')
      + '<span class="sub mono">' + id + '</span>' + (h.address ? '<span class=sub>' + esc(h.address) + '</span>' : '') + route(h) + '</td>'
    + '<td>' + ago(h.last_seen_at) + (h.stale ? ' <span class="pill pill--warn">stale</span>' : '') + '</td>'
    + '<td class=nw>' + memory(h) + '</td><td class=nw>' + disk(h) + '</td><td>' + (Number(h.servers) || 0) + '</td>'
    + '<td>' + (h.accepting ? '<span class="pill pill--ok">open</span>' : '<span class=pill>closed</span>')
    + ' <button type=button class="btn btn--sm" data-f=accept data-id="' + id + '" data-on=' + (h.accepting ? 0 : 1) + '>' + (h.accepting ? 'Close' : 'Open') + '</button>'
    + ' <button type=button class="btn btn--sm" data-f=hostip data-id="' + id + '" data-label="' + esc(h.name) + '" data-ip="' + esc(h.public_ip || '') + '">Public IP…</button></td></tr>'
    + (orphans.length ? '<tr><td colspan=6><span class="pill pill--warn">' + plural(orphans.length, 'orphan', 'orphans') + '</span> '
      + '<span class=empty>containers on ' + esc(h.name) + ' that no server names:</span> '
      + orphans.map(l => '<span class=forph><span class=mono>' + esc(l) + '</span><button type=button class="btn btn--sm" data-f=orphan data-id="' + id
        + '" data-label="' + esc(l) + '">Remove</button></span>').join('') + '</td></tr>' : '');
}

// --- hosted servers ---
function planCell(s){
  const lim = s.limits || {}, quota = Number(s.quota_mb) || 0, own = s.overrides || {};
  const size_ = [s.memory_mb ? size(s.memory_mb) : '', s.cpus ? s.cpus + ' CPU' : ''].filter(Boolean).join(' · ');
  const mine = Object.keys(own).map(k => k + ' ' + own[k]).join(', ');
  return esc(s.plan || lim.plan || '') + (mine ? ' <span class=pill title="' + esc('its own limits: ' + mine) + '">custom</span>' : '')
    + (quota ? '<span class=sub>' + size(quota) + ' quota</span>' : '')
    + (size_ ? '<span class=sub title="the memory limit and CPUs of the container">' + size_ + '</span>' : '');
}
function stateCell(s){
  const r = s.report || {}, ag = r.agent || {}, jobs = s.jobs || {};
  let out = '<span class="' + (SRV_PILL[s.state] || 'pill') + '">' + esc(words(s.state)) + '</span>';
  if (s.read_only && s.state !== 'read_only') out += ' <span class="pill pill--warn">read-only</span>';
  if (ag.last_seen_at && s.state !== 'deleted') {
    const word = !ag.running ? 'down' : ag.health === 'unhealthy' ? 'unhealthy' : 'up';
    out += ' <span class="pill ' + (word === 'up' ? 'pill--ok' : 'pill--warn') + '" title="' + esc('container ' + (ag.health || 'running') + (ag.memory_mb ? ', ' + size(ag.memory_mb) + ' memory' : '') + (ag.started_at ? ', started ' + ag.started_at : '')) + '">' + word + '</span>';
    if (ag.oom_killed) out += ' <span class="pill pill--warn" title="Docker killed its process for using all its memory">out of memory</span>';
    const use = [ag.memory_mb ? size(ag.memory_mb) + ' memory' + spark(s, 'memory_mb', size, 'memory') : '',
      ag.cpu_pct != null ? 'CPU ' + ag.cpu_pct + ' %' + spark(s, 'cpu_pct', pct, 'CPU') : '',
      Number(ag.restarts) ? plural(Number(ag.restarts), 'restart', 'restarts') : ''].filter(Boolean);
    out += use.map(u => '<span class="sub fnw" title="from the agent\'s last report">' + u + '</span>').join('');
  }
  if (r.note) out += '<span class=sub>' + esc(r.note) + '</span>';
  const busy = (Number(jobs.queued) || 0) + (Number(jobs.running) || 0), failed = Number(jobs.failed) || 0;
  if (busy) out += '<span class=sub>' + plural(busy, 'job', 'jobs') + ' in flight</span>';
  if (failed) out += '<span class=sub>' + plural(failed, 'failed job', 'failed jobs') + '</span>';
  return out;
}
function versionCell(s){
  const r = s.report || {}, id = esc(s.id), label = esc(s.label);
  let out = tagRun(s) ? '<span class=mono title="' + esc(s.image || '') + '">' + esc(tagRun(s)) + '</span>' : '<span class=empty>default</span>';
  if (outdated(s)) {
    const image = s.outdated_why === 'image', why = image ? 'newer image of ' + tagRun(s) : 'default is ' + defTag;
    out += ' <span class="pill pill--warn" title="' + esc(image ? 'the registry has a newer image for ' + tagRun(s) + ' than the one it runs'
      : 'it runs another tag than the default, ' + defTag) + '">outdated</span>';
    if (s.host_id && defTag && s.state !== 'provisioning') out += ' <button type=button class="btn btn--sm" data-f=upgrade data-id="' + id + '" data-label="' + label + '">Upgrade</button>';
    out += '<span class=sub>' + esc(why) + '</span>';
  }
  return out + (r.version ? '<span class=sub>Gamma ' + esc(r.version) + '</span>' : '');
}
function dataCell(s){
  const r = s.report || {}, ag = r.agent || {}, quota = Number(s.quota_mb) || 0;
  const used = r.uploads_bytes != null ? r.uploads_bytes / 1048576 : r.data_bytes != null ? r.data_bytes / 1048576 : ag.data_mb;
  if (used == null) return '<span class=empty>no report</span>';
  const disk = r.data_bytes != null ? r.data_bytes / 1048576 : ag.data_mb;
  return size(used) + (quota ? ' / ' + size(quota) + meter(quota, used, 0, size(used) + ' of the ' + size(quota) + ' quota') : '')
    + (disk != null ? '<span class=sub>' + size(disk) + ' on disk' + spark(s, 'data_mb', size, 'data on disk') + '</span>' : '')
    + (r.accounts != null ? '<span class=sub>' + plural(Number(r.accounts) || 0, 'account', 'accounts')
      + (r.active_accounts != null ? ', ' + (Number(r.active_accounts) || 0) + ' active this week' : '') + '</span>' : '');
}
// the last sync and the agent's last report, with the container's last write and its server errors between its last two syncs
function reportCell(s){
  const r = s.report || {}, ag = r.agent || {};
  return ago(s.synced_at) + (ag.last_seen_at ? '<span class=sub>agent ' + ago(ag.last_seen_at) + '</span>' : '')
    + (r.last_write_at ? '<span class=sub>last write ' + ago(r.last_write_at) + '</span>' : '')
    + (Number(r.errors) ? '<span class=sub title="answers of 500 and up between its last two syncs">'
      + plural(Number(r.errors), 'server error', 'server errors') + '</span>' : '');
}
function actionsCell(s){
  if (s.state === 'deleted') return '';
  const placed = !!s.host_id, o = [];
  if (placed) o.push(['restart', 'Restart'], ['stop', 'Stop…'], ['start', 'Start'], ['logs', 'Logs']);
  o.push(['history', 'History'], ['limits', 'Limits…'], ['env', 'Environment…']);
  o.push(s.state === 'suspended' ? ['resume', 'Resume'] : ['suspend', 'Suspend…']);
  if (placed) o.push(['upgrade', 'Upgrade to…'], ['rollback', 'Roll back…']);
  o.push(['delete', 'Delete…']);
  return '<select class=sm data-f=srv data-id="' + esc(s.id) + '" data-label="' + esc(s.label) + '" aria-label="Actions"><option value="">Actions…</option>'
    + o.map(([v, t]) => '<option value=' + v + '>' + t + '</option>').join('') + '</select>';
}
// a server on a routed host: ok once its DNS record points at the host, else pending (the last error on hover)
function dnsPill(s){
  if (!s.dns) return '';
  const err = (s.report || {}).dns || {}, at = err.at ? new Date(err.at) : null;
  const title = s.dns === 'ok' ? 'its DNS record points at ' + s.host_ip
    : err.error ? 'the last try' + (at && !isNaN(at) ? ', ' + at.toLocaleString() + ',' : '') + ' failed: ' + err.error
    : 'no record for ' + s.host_ip + ' yet';
  return ' <span class="pill ' + (s.dns === 'ok' ? 'pill--ok' : 'pill--warn') + '" title="' + esc(title) + '">dns ' + esc(s.dns) + '</span>';
}
function serverRow(s){
  const gone = s.state === 'deleted';
  const name = s.url && !gone ? '<a href="' + esc(s.url) + '" target=_blank rel=noopener><b>' + esc(s.label) + '</b></a>' : '<b>' + esc(s.label) + '</b>';
  return '<tr' + (gone ? ' class=fgone' : '') + '><td>' + name + '<span class="sub mono">' + esc(s.id) + '</span>' + (s.host ? '<span class=sub>on ' + esc(s.host) + dnsPill(s) + '</span>' : '') + '</td>'
    + '<td>' + esc(s.username || '(deleted account)') + '<span class="sub mono">' + esc(s.account_id) + '</span></td>'
    + '<td class=nw>' + planCell(s) + '</td><td>' + stateCell(s) + '</td><td>' + versionCell(s) + '</td><td class=nw>' + dataCell(s) + '</td>'
    + '<td class=nw>' + reportCell(s) + '</td><td>' + actionsCell(s) + '</td></tr>';
}

// --- jobs ---
// What the job does beyond its kind: an upgrade's tag (none: a resize to the plan's size), and
// for one of a run (an upgrade's or an update's), its wave and the run's progress (wave_done of
// wave_total servers done).
function jobWhat(j){
  const p = j.payload || {}, m = String(j.wave || '').match(/^(.*)\/(\d+)$/);
  let out = j.kind === 'upgrade' ? '<span class=sub>' + (p.tag ? 'to <span class=mono>' + esc(p.tag) + '</span>' : 'resize'
    + (p.memory_mb ? ' to ' + size(p.memory_mb) : '')) + '</span>' : '';
  if (m) out += '<span class=sub title="' + esc('run ' + m[1]) + '">wave ' + Number(m[2])
    + (j.wave_total ? ' · ' + (Number(j.wave_done) || 0) + '/' + j.wave_total + ' done' : '') + '</span>';
  return out;
}
function jobRow(j){
  const full = resultText(j), short = full.length > 160 ? full.slice(0, 160) + '…' : full, id = esc(j.id);
  const btn = (f, text) => '<button type=button class="btn btn--sm" data-f=' + f + ' data-id="' + id + '" data-sid="' + esc(j.server_id) + '" data-label="' + esc(j.label || '') + '">' + text + '</button> ';
  const btns = (j.kind === 'logs' && j.state === 'done' ? btn('view', 'View') : '')
    + (['failed', 'canceled'].includes(j.state) ? btn('retry', 'Retry') : '') + (['queued', 'held', 'failed'].includes(j.state) ? btn('cancel', 'Cancel') : '');
  return '<tr><td>' + ago(j.created_at) + '</td><td>' + esc(KIND_WORD[j.kind] || j.kind) + jobWhat(j) + (j.attempts > 1 ? '<span class=sub>attempt ' + j.attempts + '</span>' : '') + '</td>'
    + '<td>' + esc(j.label || j.server_id || '') + '</td><td>' + esc(j.host || j.host_id || '') + '</td>'
    + '<td><span class="' + (JOB_PILL[j.state] || 'pill') + '">' + esc(j.state) + '</span></td><td>' + took(j) + '</td>'
    + '<td class=fres title="' + esc(full) + '">' + esc(short) + '</td><td>' + btns + '</td></tr>';
}

// --- the summary strip ---
function summary(hosts, list, jobs){
  const stale = hosts.filter(h => h.stale).length, alive = list.filter(s => s.state !== 'deleted'), by = {};
  alive.forEach(s => { by[s.state] = (by[s.state] || 0) + 1; });
  const jc = {queued: 0, running: 0, failed: 0};
  list.forEach(s => { for (const k in jc) jc[k] += Number((s.jobs || {})[k]) || 0; });
  jobs.forEach(j => { if (!j.server_id && j.state in jc) jc[j.state]++; });   // a host's own jobs (an orphan's removal)
  live = jc.queued + jc.running > 0 || jobs.some(j => j.state === 'queued' || j.state === 'running');
  const old = alive.filter(outdated).length, repo = defImage.slice(0, defImage.length - defTag.length).replace(/:$/, '');
  const tile = (label, value, detail, cls) => '<div><span>' + label + '</span><b' + (cls ? ' class=' + cls : '') + '>' + value + '</b><small>' + detail + '</small></div>';
  $('fsum').innerHTML = tile('Hosts', hosts.length, hosts.length ? (hosts.length - stale) + ' fresh' + (stale ? ' · <span class="pill pill--warn">' + stale + ' stale</span>' : '') : 'none yet')
    + tile('Servers', alive.length, ORDER.filter(k => by[k]).map(k => by[k] + ' ' + words(k)).join(' · ') || 'none yet')
    + tile('Jobs in flight', jc.queued + jc.running, jc.queued + ' queued · ' + jc.running + ' running'
      + (jc.failed ? ' · <span class="pill pill--warn">' + jc.failed + ' failed</span>' : '') + (live ? '<span class=sub>refreshing every 4 s</span>' : ''))
    + tile('Default image', esc(defTag || 'unknown'), esc(repo) + (old ? ' · <span class="pill pill--warn">' + old + ' outdated</span>' : '')
      + (autoOn ? '<span class=sub>upgraded automatically</span>' : ''), 'mono');
}

// --- loading, and the 4 s refresh while jobs run ---
const shown = () => !document.hidden && !TAB.hidden;
function schedule(){ clearTimeout(timer); timer = live && shown() ? setTimeout(tick, 4000) : 0; }
// an open Actions menu is not pulled from under the admin: wait for it
function tick(){ const a = document.activeElement; if (!shown()) return; if (a && a.tagName === 'SELECT' && a.closest('#tab-servers tbody')) schedule(); else refresh(); }
async function refresh(){
  clearTimeout(timer); timer = 0;
  const seq = ++gen, st = $('fjstate').value, err = $('ferr');
  let h, s, j, fe;
  try {
    [h, s, j, fe] = await Promise.all([api('/api/admin/hosts', undefined, 'GET'), api('/api/admin/servers', undefined, 'GET'),
      api('/api/admin/jobs?limit=100' + (st ? '&state=' + encodeURIComponent(st) : ''), undefined, 'GET'),
      api('/api/admin/fleet-env', undefined, 'GET')]);
  } catch (e) {
    if (e.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
    if (seq === gen) { err.hidden = false; err.textContent = 'Could not load the fleet: ' + e.message; schedule(); }
    return;
  }
  if (seq !== gen) return;
  err.hidden = true;
  servers = s.servers || []; defImage = s.default_image || ''; defTag = tagOf(defImage); autoOn = !!s.auto_upgrade;
  fleetEnv = fe.names || [];
  const hosts = h.hosts || [], jobs = j.jobs || [], gone = servers.filter(x => x.state === 'deleted').length;
  summary(hosts, servers, jobs);
  showEnv();
  const old = servers.filter(x => outdated(x) && x.host_id && UPGRADABLE.includes(x.state)).length, ob = TAB.querySelector('[data-f=upgold]');
  ob.textContent = 'Upgrade all outdated (' + old + ')'; ob.disabled = !old; ob.dataset.n = old;
  $('fhosts').innerHTML = hosts.map(hostRow).join('') || '<tr><td colspan=6 class=empty>No hosts. Add one below, then start its agent with the token (cloud/fleet/deploy/README.md).</td></tr>';
  const rows = servers.filter(x => showDeleted || x.state !== 'deleted');
  $('fservers').innerHTML = rows.map(serverRow).join('') || '<tr><td colspan=8 class=empty>No hosted servers.</td></tr>';
  $('fsrvhead').innerHTML = 'one per account on a hosted plan' + (gone ? ' · <button type=button class=linkbtn data-f=deleted>' + (showDeleted ? 'hide' : 'show') + ' ' + gone + ' deleted</button>' : '');
  $('fjobs').innerHTML = jobs.map(jobRow).join('') || '<tr><td colspan=8 class=empty>No jobs' + (st ? ' ' + esc(st) : '') + '.</td></tr>';
  schedule();
}
document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(timer); timer = 0; } else if (live && shown()) refresh(); });
$('fjstate').onchange = () => refresh();

// --- the log viewer: Logs enqueues a job, the job is polled every 2 s ---
function logNote(text){ const n = $('flognote'); n.hidden = !text; n.textContent = text || ''; }
function logOpen(label){ $('flogs').hidden = false; $('flogwho').textContent = label; $('flogtext').hidden = true; $('flogstate').textContent = ''; }
async function openLogs(id, label){
  clearTimeout(logTimer); logJob = ''; logFor = {id, label};
  logOpen(label); logNote('Fetching the log of ' + label + '…'); $('flogs').scrollIntoView({block: 'nearest'});
  try { const r = await api('/api/admin/servers/' + id + '/logs', {}); watchLog(r.job.id, id, label); refresh(); }
  catch (e) { logNote('Could not ask for the log: ' + e.message); }
}
function watchLog(jobId, id, label){
  clearTimeout(logTimer); logJob = jobId; logFor = {id, label};
  logOpen(label); logNote('Fetching the log of ' + label + '…'); $('flogs').scrollIntoView({block: 'nearest'});
  pollLog(jobId, 0);
}
async function pollLog(jobId, n){
  if (jobId !== logJob) return;
  let j;
  try { j = (await api('/api/admin/jobs/' + encodeURIComponent(jobId), undefined, 'GET')).job; }
  catch (e) { if (jobId === logJob) logNote('Could not read the job: ' + e.message); return; }
  if (jobId !== logJob) return;
  if (j.state === 'done') {
    let r = j.result; if (typeof r === 'string') { try { r = JSON.parse(r); } catch (e) { r = {lines: [r]}; } }
    const lines = r && Array.isArray(r.lines) ? r.lines : [], pre = $('flogtext');
    pre.textContent = lines.join('\n') || '(the log is empty)'; pre.hidden = false; logNote('');
    const since = r && r.since ? new Date(r.since) : null;
    $('flogstate').textContent = 'The last ' + plural(lines.length, 'line', 'lines') + (r && r.container ? ' of ' + r.container : '')
      + (since && !isNaN(since) ? ', since ' + since.toLocaleString() : '') + (r && r.truncated ? ' (the oldest cut)' : '')
      + ' · fetched ' + new Date(j.finished_at || Date.now()).toLocaleTimeString();
    pre.scrollTop = pre.scrollHeight;
  } else if (j.state === 'failed' || j.state === 'canceled') logNote('The log could not be fetched: ' + (resultText(j) || j.state) + '.');
  else if (n >= 90) logNote('No answer from the agent after three minutes; is its host stale? The job stays ' + j.state + ' in the Jobs table.');
  else { logNote('Fetching the log of ' + logFor.label + '… (' + j.state + ')'); logTimer = setTimeout(() => pollLog(jobId, n + 1), 2000); }
}
function closeLog(){ clearTimeout(logTimer); logJob = ''; logFor = null; $('flogs').hidden = true; }

// --- a server's history (Actions → History): its hourly samples of the last 7 or 30 days, one line each ---
const HISTORY = [['memory_mb', 'Memory', size], ['cpu_pct', 'CPU', pct], ['data_mb', 'Data on disk', size],
  ['accounts', 'Accounts', String], ['active_accounts', 'Active this week', String], ['errors', 'Server errors', String],
  ['restarts', 'Restarts', String]];
let histFor = null;
async function showHistory(){
  const who = histFor, hours = Number($('fhistdays').value);
  if (!who) return;
  $('fhiststate').textContent = 'Loading…';
  let d;
  try { d = await api('/api/admin/metrics?kind=server&ref=' + encodeURIComponent(who.id) + '&hours=' + hours, undefined, 'GET'); }
  catch (e) { if (who === histFor) $('fhiststate').textContent = 'Could not load the history: ' + e.message; return; }
  if (who !== histFor) return;
  const p = d.points || [], t1 = Date.now(), t0 = t1 - hours * 3600e3;
  $('fhiststate').textContent = plural(p.length, 'hour', 'hours') + ' with a report';
  $('fhistcharts').innerHTML = HISTORY.map(([k, name, fmt]) => {
    const v = p.map(x => x[k]).filter(x => x != null), last = v[v.length - 1];
    return '<div><b>' + name + '</b><span class=sub>' + (v.length ? 'min ' + fmt(Math.min(...v)) + ' · max ' + fmt(Math.max(...v)) + ' · last ' + fmt(last)
      : 'no samples') + '</span>' + line(p, k, t0, t1, 300, 60, 'fchart', name + ', last ' + (v.length ? fmt(last) : 'unknown')) + '</div>';
  }).join('');
}
function openHistory(id, label){
  histFor = {id, label}; $('fhistwho').textContent = label; $('fhistcharts').innerHTML = '';
  $('fhist').hidden = false; $('fhist').scrollIntoView({block: 'nearest'}); showHistory();
}
$('fhistdays').onchange = () => showHistory();

// --- a server's own limits (Actions → Limits…): an empty field is the plan's number ---
function showLimits(s){
  const f = $('flimform').elements, own = s.overrides || {}, plan = s.plan_limits || {};
  $('flimwho').textContent = s.label + ' · ' + ((s.limits || {}).plan || s.plan || '') + ' plan';
  LIMITS.forEach(k => { f[k].value = own[k] != null ? own[k] : ''; f[k].placeholder = plan[k] != null ? plan[k] : ''; });
}
function openLimits(id){
  const s = servers.find(x => x.id === id); if (!s) return;
  limFor = id; showLimits(s); $('flimform').querySelector('.msg').textContent = '';
  $('flim').hidden = false; $('flim').scrollIntoView({block: 'nearest'});
}
async function saveLimits(overrides, msg){
  const r = await api('/api/admin/servers/' + encodeURIComponent(limFor), {overrides}, 'PATCH');
  showLimits(r.server); say(msg, 'Saved.'); refresh();
}

// --- the extra environment: every server's, or one server's (envFor, from Actions → Environment…) ---
function showEnv(){
  const s = envFor ? servers.find(x => x.id === envFor) : null;
  if (!s) envFor = null;
  const own = s ? s.env_names || [] : fleetEnv;
  const rm = n => '<button type=button class="btn btn--sm" data-f=envrm data-name="' + esc(n) + '">Remove</button>';
  let rows = own.map(n => '<tr><td class=mono>' + esc(n) + '</td><td>' + (s ? 'this server' : 'every server') + '</td><td>' + rm(n) + '</td></tr>');
  if (s) rows = rows.concat(fleetEnv.map(n => '<tr><td class=mono>' + esc(n) + '</td><td class=empty>every server'
    + (own.includes(n) ? '; this server\'s own wins' : '') + '</td><td></td></tr>'));
  $('fenvlist').innerHTML = rows.join('') || '<tr><td colspan=3 class=empty>No variables: a container gets only what the fleet sets itself.</td></tr>';
  $('fenvwho').innerHTML = s ? esc(s.label) + '\'s own, over every server\'s · <button type=button class=linkbtn data-f=envfleet>back to every server\'s</button>'
    : 'extra variables for every hosted container';
  $('fenvall').hidden = !!s;
  $('fenvnote').textContent = (s ? 'Saving applies it to ' + s.label + ' now when its container runs: an update job rebuilds it on its image.'
    : 'Saving changes nothing that runs: a new server gets the variables, and Apply rebuilds the running containers on their images with them, a wave at a time.')
    + ' A value is never shown again; to change one, save it anew.';
}
function openEnv(id){ envFor = id; showEnv(); $('fenvform').querySelector('.msg').textContent = ''; $('fenv').scrollIntoView({block: 'nearest'}); }
function saveEnv(body){
  return envFor ? api('/api/admin/servers/' + encodeURIComponent(envFor), {env: body}, 'PATCH') : api('/api/admin/fleet-env', body, 'PATCH');
}

// --- the default image tag and automatic upgrades (the server settings fleet_image_tag and fleet_auto_upgrade) ---
function showDefaults(v){
  const f = $('fdef').elements;
  f.tag.value = v.fleet_image_tag || ''; f.tag.placeholder = v.fleet_image_tag_default || '';
  f.auto.value = v.fleet_auto_upgrade ? 'on' : 'off';
  $('fdefnote').textContent = 'A new server runs the default tag; a server on another tag, or on an older image of it, is outdated. '
    + 'Blank is the environment\'s tag, ' + (v.fleet_image_tag_default || '') + '. With automatic upgrades on, every hour the outdated servers '
    + 'are upgraded to it one at a time, and a failed upgrade stops them until you retry or cancel it.';
}
async function loadDefaults(){
  try { showDefaults(await api('/api/admin/settings', undefined, 'GET')); }
  catch (e) { $('fdef').querySelector('.msg').textContent = 'Could not load the default tag: ' + e.message; }
}

// --- actions ---
const ASK = {
  stop: l => 'Stop ' + l + '? Its container stops; the lifecycle state stays, and Start brings it back.',
  suspend: l => 'Suspend ' + l + '? It turns read-only, and the lifecycle leaves it alone until you resume it.',
  rollback: l => 'Roll ' + l + ' back to the container its failed upgrade kept? The live one is removed and the kept one starts in its place. Gamma may already have migrated the data, which the older image can refuse.',
  delete: l => 'Delete ' + l + ' now? Its container, data and off-site copies are removed. This cannot be undone.'};
async function serverAction(id, label, v){
  if (v === 'logs') return openLogs(id, label);
  if (v === 'history') return openHistory(id, label);
  if (v === 'limits') return openLimits(id);
  if (v === 'env') return openEnv(id);
  if (ASK[v] && !await ask(ASK[v](label), {ok: v.charAt(0).toUpperCase() + v.slice(1), danger: true})) return;
  const base = '/api/admin/servers/' + encodeURIComponent(id) + '/';
  if (v === 'upgrade') { const tag = await ask('Upgrade ' + label + ' to which image tag?', {input: defTag, ok: 'Upgrade'}); if (!tag) return; await api(base + 'upgrade', {tag}); }
  else await api(base + v, {});
  await refresh();
}
TAB.addEventListener('change', async (ev) => {
  const sel = ev.target; if (!sel.matches('select[data-f=srv]')) return;
  const v = sel.value; sel.value = ''; sel.blur(); if (!v) return;
  try { await serverAction(sel.dataset.id, sel.dataset.label, v); }
  catch (e) { if (e.status === 401) location.href = '/login?next=' + encodeURIComponent(location.pathname); else toast(e.message); }
});
TAB.addEventListener('click', async (ev) => {
  const b = ev.target.closest('button[data-f]'); if (!b) return;
  const id = b.dataset.id, label = b.dataset.label, go = (fn) => act(b, async () => { await fn(); await refresh(); });
  switch (b.dataset.f) {
    case 'accept': go(() => api('/api/admin/hosts/' + encodeURIComponent(id), {accepting: b.dataset.on === '1'}, 'PATCH')); break;
    case 'hostip': {
      const ip = await ask('The public IP of ' + label + '? With one, the host runs its own Caddy and each server on it gets a DNS record; '
        + 'blank puts it behind the account server\'s Caddy.', {input: b.dataset.ip || '', blank: true, ok: 'Save'});
      if (ip !== null) go(() => api('/api/admin/hosts/' + encodeURIComponent(id), {public_ip: ip}, 'PATCH'));
      break;
    }
    case 'orphan': if (await ask('Remove the container ' + label + '? No server row names it; the agent removes the container and its data directory.', {ok: 'Remove', danger: true}))
      go(() => api('/api/admin/hosts/' + encodeURIComponent(id) + '/orphans/' + encodeURIComponent(label) + '/remove', {})); break;
    case 'upgrade': if (await ask('Upgrade ' + label + ' to ' + defTag + '? Its container restarts on the new image; the old one stays until the new one is healthy.', {ok: 'Upgrade'}))
      go(() => api('/api/admin/servers/' + encodeURIComponent(id) + '/upgrade', {tag: defTag})); break;
    case 'retry': case 'cancel': go(() => api('/api/admin/jobs/' + encodeURIComponent(id) + '/' + b.dataset.f, {})); break;
    case 'view': watchLog(id, b.dataset.sid, label); break;
    case 'deleted': showDeleted = !showDeleted; refresh(); break;
    case 'logagain': if (logFor) openLogs(logFor.id, logFor.label); break;
    case 'logclose': closeLog(); break;
    case 'histclose': histFor = null; $('fhist').hidden = true; break;
    case 'limplan': {
      const msg = $('flimform').querySelector('.msg');
      if (limFor && await ask('Give this server its plan\'s limits again?', {ok: 'Back to the plan\'s'}))
        act(b, async () => { await saveLimits(Object.fromEntries(LIMITS.map(k => [k, null])), msg); b.disabled = false; }, msg);
      break;
    }
    case 'limclose': limFor = null; $('flim').hidden = true; break;
    case 'envrm': {
      const name = b.dataset.name, s = servers.find(x => x.id === envFor);
      const text = s ? 'Remove ' + name + ' from ' + s.label + '? An update job rebuilds its container without it'
          + (fleetEnv.includes(name) ? ', with every server\'s value instead' : '') + '.'
        : 'Remove ' + name + ' from every server\'s environment? Running containers keep it until the next Apply.';
      if (await ask(text, {ok: 'Remove', danger: true})) go(() => saveEnv({unset: [name]}));
      break;
    }
    case 'envfleet': envFor = null; showEnv(); break;
    case 'upgold': {
      const f = $('fupg'), n = Math.max(1, Number(f.elements.wave_size.value) || 1), msg = f.querySelector('.msg');
      if (await ask('Upgrade the ' + plural(Number(b.dataset.n) || 0, 'outdated server', 'outdated servers') + ' to ' + defTag + ', ' + n
          + ' at a time? Each container restarts on the newest image of it.', {ok: 'Upgrade'}))
        act(b, async () => {
          const r = await api('/api/admin/servers/upgrade', {outdated: true, wave_size: n});
          say(msg, plural(r.jobs, 'server', 'servers') + ' in ' + plural(r.waves, 'wave', 'waves') + ', run ' + r.run + '.'); await refresh();
        }, msg);
      break;
    }
  }
});

// --- the forms ---
bind('fhost', async (d, msg) => {
  const r = await api('/api/admin/hosts', {name: d.name, address: d.address, public_ip: d.public_ip}), box = $('fhosttoken');
  box.hidden = false;
  box.textContent = 'GAMMA_FLEET_HOST_TOKEN=' + r.token + '\n\nShown once. On a fresh host, as root, this installs the agent with it'
    + (r.host.public_ip ? ' and the host\'s own Caddy' : '') + ':\n\n' + r.bootstrap
    + '\n\nOn a host that runs the agent already, the token goes into its .env (cloud/fleet/deploy/README.md), then docker compose up -d.';
  say(msg, 'Host ' + r.host.name + ' added.'); $('fhost').reset(); refresh();
});
// the username search fills the account id (the id can also be pasted)
const who = $('fwho'), whoId = $('fwhoid'), provMsg = $('fprov').querySelector('.msg');
let found = {}, whoTimer = 0;
function pick(a){
  whoId.value = a.id; provMsg.classList.remove('ok');
  const has = servers.find(s => s.account_id === a.id && s.state !== 'deleted');
  if (has) provMsg.textContent = a.username + ' already has a server, ' + has.label + '.';
  else if (!(a.plan in QUOTAS)) provMsg.textContent = a.username + ' is on the ' + a.plan + ' plan, which has no hosted server.';
  else if (!a.email_verified) provMsg.textContent = a.username + ' has not confirmed their e-mail yet.';
  else say(provMsg, a.username + ' · ' + a.plan + ' · ' + a.email);
}
who.addEventListener('input', () => {
  const q = who.value.trim().toLowerCase();
  clearTimeout(whoTimer);
  if (!q) { whoId.value = ''; provMsg.textContent = ''; return; }
  if (found[q]) { pick(found[q]); return; }
  whoId.value = ''; provMsg.textContent = ''; provMsg.classList.remove('ok');   // the text no longer names the account picked before
  if (q.length < 2) return;
  whoTimer = setTimeout(async () => {
    try {
      const d = await api('/api/admin/accounts?limit=8&q=' + encodeURIComponent(q), undefined, 'GET');
      found = {}; d.accounts.filter(a => !a.deleted_at).forEach(a => { found[a.username] = a; });
      $('fwholist').innerHTML = Object.values(found).map(a => '<option value="' + esc(a.username) + '">' + esc(a.plan + ' · ' + a.email) + '</option>').join('');
      const now = found[who.value.trim().toLowerCase()]; if (now) pick(now);
    } catch (e) {}
  }, 250);
});
bind('fprov', async (d, msg) => {
  const r = await api('/api/admin/servers/provision', {account_id: d.account_id.trim()});
  say(msg, r.server.label + ': ' + words(r.server.state) + '.'); who.value = ''; whoId.value = ''; refresh();
});
bind('fupg', async (d, msg) => {
  const tag = d.tag.trim(), n = Math.max(1, Number(d.wave_size) || 1);
  if (!await ask('Upgrade every running server to ' + tag + ', ' + n + ' at a time? Each container restarts on the new image.', {ok: 'Upgrade'})) return;
  const r = await api('/api/admin/servers/upgrade', {tag, wave_size: n});
  say(msg, plural(r.jobs, 'server', 'servers') + ' in ' + plural(r.waves, 'wave', 'waves') + ', run ' + r.run + '.'); refresh();
});
bind('fdef', async (d, msg) => {
  if (d.auto === 'on' && !autoOn && !await ask('Upgrade outdated servers automatically? Every hour, each one is upgraded to the default tag, one at a time.', {ok: 'Turn on'})) {
    $('fdef').elements.auto.value = 'off'; return;
  }
  showDefaults(await api('/api/admin/settings', {fleet_image_tag: d.tag.trim(), fleet_auto_upgrade: d.auto}, 'PATCH'));
  say(msg, 'Saved.'); refresh();
});
bind('flimform', (d, msg) => saveLimits(Object.fromEntries(LIMITS.map(k => [k, String(d[k] || '').trim() === '' ? null : Number(d[k])])), msg));
bind('fenvform', async (d, msg) => {
  const name = d.name.trim(), s = servers.find(x => x.id === envFor);
  await saveEnv({set: {[name]: d.value}});
  $('fenvform').reset();
  say(msg, name + ' saved' + (!s ? '. Apply to every server to give it to the running ones.'
    : s.host_id && UPGRADABLE.includes(s.state) ? '; an update job applies it now.' : '; its container gets it when it is made or next updated.'));
  refresh();
});
bind('fenvrun', async (d, msg) => {
  const n = Math.max(1, Number(d.wave_size) || 1);
  if (!await ask('Rebuild every running server\'s container on its image with the environment as it is now, ' + n + ' at a time?', {ok: 'Apply'})) return;
  const r = await api('/api/admin/servers/apply-env', {wave_size: n});
  say(msg, plural(r.jobs, 'server', 'servers') + ' in ' + plural(r.waves, 'wave', 'waves') + ', run ' + r.run + '.'); refresh();
});
// the tab's entry: the settings the Upgrade form shows are read once per opening, the rest by refresh
return () => { loadDefaults(); return refresh(); };
})();
""".replace("__QUOTAS__", json.dumps(_QUOTAS))

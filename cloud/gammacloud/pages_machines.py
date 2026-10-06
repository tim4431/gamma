"""The Admin page's Machines tab (docs/dev/hosted.md "Admin"): the hosts a
fleet agent runs on, each a card with its capacity and containers, and
under the cards the one selected: its placement, public IP, name, agent
token and removal; every container on it, the hosted servers' and the
host's own (the account server, the share host, Caddy, the demo, the agent
itself) with its image, state, use and actions; their logs; and the host's
recent jobs. All of it comes from ``GET /api/admin/machines``.

``pages.admin_page`` includes ``ADMIN_TAB`` and appends ``ADMIN_JS``,
which defines ``loadMachines(id)``, the tab's entry, and
``pickMachine(id)``, which selects a host the way ``#machines/<host id>``
does (a card is a link to it). It uses ``fleetUI``
(``pages_fleet.SHARED_JS``) and the admin script's ``api``, ``esc``,
``bind``, ``say``, ``act``, ``ask`` and ``toast``; clicks are delegated to
the tab through ``data-m`` attributes. The tab reloads every 10 s while a
job of the selected host is queued or running and the page is in view,
and not at all otherwise. After a restart, update or rollback of a
container this page is served through (the account server's own, or the
Caddy of its project) it waits for the server to go and come back, then
reloads itself. The ``<style>`` block holds only the cards and the cells
the shared one has no rule for."""

STYLE = """<style>
#tab-machines .mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-bottom:16px}#tab-machines .mcards{display:contents}
#tab-machines .mcard{display:block;min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px;color:var(--text)}
#tab-machines a.mcard:hover{border-color:var(--line-2);text-decoration:none}#tab-machines a.mcard.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
#tab-machines .mcard>b{font-size:15px;font-weight:600}#tab-machines .mpills{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 10px}
#tab-machines .mcard .cap{max-width:none}#tab-machines .mcard .sub+.cap{margin-top:3px}#tab-machines .mcount{margin-top:10px;font-size:13px;color:var(--text-2)}
#tab-machines .madd{border-style:dashed;display:flex;flex-direction:column;justify-content:center}#tab-machines .madd>.btn{align-self:center}
#tab-machines .madd label{margin:8px 0 4px}#tab-machines .madd .actions{margin-top:12px}
#tab-machines td.mname>b{white-space:nowrap}#tab-machines td.mimg{min-width:140px;max-width:240px;overflow-wrap:anywhere}
#tab-machines td.macts{min-width:180px}#tab-machines .macts>span{display:flex;flex-wrap:wrap;gap:4px}
</style>"""

ADMIN_TAB = (
    "<div id=tab-machines class=fleet hidden>" + STYLE
    + "<div class=notice id=merr hidden></div><div class=notice id=mback hidden></div>"
    "<p class=empty id=mnone hidden>No machines yet. Add one, then start its agent with the token it shows "
    "(cloud/fleet/deploy/README.md).</p>"
    # the cards, and the one that adds a machine (outside the cards drawn again on each load, so its form keeps what
    # is typed)
    "<div class=mgrid><div class=mcards id=mcards></div><div class='mcard madd'>"
    "<button type=button class='btn btn--sm' data-m=addopen>+ Add machine</button>"
    "<form id=madd autocomplete=off hidden><b>Add a machine</b>"
    "<label>Name<input name=name required placeholder='vps-2'></label>"
    "<label>Note<input name=address placeholder='optional, for you'></label>"
    "<label>Public IP<input name=public_ip placeholder='blank: behind this Caddy' spellcheck=false></label>"
    "<div class=actions><button type=submit class='btn btn--primary btn--sm'>Add</button>"
    "<button type=button class='btn btn--sm' data-m=addcancel>Cancel</button></div><div class=msg></div>"
    "<p class=fnote>A machine with a public IP runs a Caddy of its own, and each server on it gets a DNS record at "
    "Cloudflare, so it takes servers only while the Cloudflare token is set. Leave it blank for the one beside the "
    "account server's Caddy.</p></form></div></div>"
    # the machine selected
    "<div id=msel hidden><div class=section><h2><b id=mhead></b><span id=mseen></span></h2>"
    "<div class=body><div class=mpills id=mpills></div><div class=toolbar id=mtools></div><p class=fnote id=mnote></p></div>"
    "<div class=body id=mtoken hidden><div class=secretbox id=mtokentext></div>"
    "<div class=actions><button type=button class='btn btn--sm' data-m=tokendone>Done</button></div></div></div>"
    "<div class=section><h2>Containers <span id=mchead></span></h2><div class='body tbl'><table><thead><tr>"
    "<th>Container</th><th>Image</th><th>Status</th><th>Up</th><th>Use</th><th></th>"
    "</tr></thead><tbody id=mrows></tbody></table></div></div>"
    # the log viewer (fleetUI.logViewer), opened by a container's Logs
    "<div class=section id=mlogs hidden><h2>Logs <span id=mlogwho></span></h2><div class=body>"
    "<div class=toolbar><span class=empty id=mlogstate></span><span class=spacer></span>"
    "<select id=mlines class=sm aria-label='Lines'><option value=200>200 lines</option>"
    "<option value=1000>1000 lines</option><option value=5000>5000 lines</option></select>"
    "<button type=button class='btn btn--sm' data-m=logagain>Fetch again</button>"
    "<button type=button class='btn btn--sm' data-m=logclose>Close</button></div>"
    "<div class='notice flognote' id=mlognote hidden></div><pre class=flog id=mlogtext hidden></pre></div></div>"
    "<div class=section><h2>Recent jobs <span>the last 20 on this machine</span></h2><div class='body tbl'><table>"
    "<thead><tr><th>Created</th><th>Job</th><th>For</th><th>State</th><th>Took</th><th>Result</th><th></th></tr>"
    "</thead><tbody id=mjobs></tbody></table></div></div></div></div>")

ADMIN_JS = r"""
// --- Machines tab (pages_machines.py) ---
const [loadMachines, pickMachine] = (() => {
const {plural, pct, size, ago, took, meter, spark, resultText, logViewer, JOB_PILL} = fleetUI;
const TAB = document.getElementById('tab-machines'), $ = id => document.getElementById(id);
const UP = ['running', 'restarting', 'paused'], DOWN = ['restarting', 'exited', 'dead'];
let machines = [], selected = '', tokenFor = '', timer = 0, gen = 0, live = false, loaded = false;
const logs = logViewer({sec: $('mlogs'), who: $('mlogwho'), state: $('mlogstate'), note: $('mlognote'), text: $('mlogtext')});

const current = () => machines.find(h => h.id === selected) || null;
const hostApi = h => '/api/admin/hosts/' + encodeURIComponent(h.id);
const byName = (a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
function upFor(ts){
  const s = (Date.now() - Date.parse(ts)) / 1000;
  if (isNaN(s)) return '';
  return s < 90 ? 'a minute' : s < 5400 ? Math.round(s / 60) + ' min' : s < 129600 ? Math.round(s / 3600) + ' h' : Math.round(s / 86400) + ' d';
}
// the containers this page is served through: the account server's own (compose service account) and its project's Caddy
function front(h, c){
  const acc = (h.containers || []).find(x => x.compose && x.compose.service === 'account');
  return !!(acc && c.compose && c.compose.project === acc.compose.project && ['account', 'caddy'].includes(c.compose.service));
}
// what Update all takes (fleet.updatable): the host's own containers with a newer image, the agent's last
const updatable = h => (h.containers || []).filter(c => !c.managed && c.image_stale === true && !c.kept).sort((a, b) => !!a.self - !!b.self);

// --- the cards ---
function pills(h){
  const p = [h.public_ip ? '<span class=pill title="its own Caddy; each server on it gets a DNS record">routed <span class=mono>' + esc(h.public_ip) + '</span></span>'
      + (h.dns === 'off' ? '<span class="pill pill--warn" title="GAMMA_CLOUD_CF_API_TOKEN and _ZONE_ID are not set: placement skips this machine">no dns token: closed</span>' : '')
    : '<span class=pill title="behind the account server\'s Caddy and the wildcard DNS record">entrance</span>'];
  p.push(!h.last_seen_at ? '<span class="pill pill--warn">no heartbeat yet</span>'
    : h.stale ? '<span class="pill pill--warn" title="' + esc('last heartbeat ' + new Date(h.last_seen_at).toLocaleString()) + '">stale</span>'
    : '<span class=pill>seen ' + ago(h.last_seen_at) + '</span>');
  if (!h.accepting) p.push('<span class=pill title="takes no new servers">closed</span>');
  if (h.agent_version) p.push('<span class=pill>agent ' + esc(h.agent_version) + '</span>'
    + (h.agent_stale ? '<span class="pill pill--warn" title="the registry has a newer image of the agent">update</span>' : ''));
  return p.join('');
}
function memory(h){
  const total = Number(h.memory_mb) || 0, com = Number(h.committed_mb) || 0, res = Number(h.reserve_mb) || 0, free = Number(h.free_mb) || 0;
  return '<span class=sub>Memory: ' + size(com) + ' of ' + size(total) + ' committed' + spark(h, 'memory_used_mb', size, 'memory in use') + '</span>'
    + meter(total, com, res, size(com) + ' committed to servers, ' + size(res) + ' kept for the machine, ' + size(free) + ' free for new servers');
}
function disk(h){
  const total = Number(h.disk_mb) || 0, used = Number(h.disk_used_mb) || 0;
  return '<span class=sub>Disk: ' + size(used) + ' of ' + size(total) + ' used' + spark(h, 'disk_used_mb', size, 'disk used') + '</span>'
    + meter(total, used, 0, size(total - used) + ' free');
}
function card(h){
  const cs = h.containers || [], run = cs.filter(c => c.status === 'running').length;
  return '<a class="mcard' + (h.id === selected ? ' on' : '') + '" href="#machines/' + esc(h.id) + '"><b>' + esc(h.name) + '</b>'
    + '<div class=mpills>' + pills(h) + '</div>'
    + (Number(h.memory_mb) ? memory(h) + disk(h) : h.last_seen_at ? '<span class=empty>no capacity reported</span>' : '')
    + '<div class=mcount>' + plural(cs.length, 'container', 'containers') + ' · ' + run + ' running'
    + (h.updates ? ' · <span class="pill pill--warn">' + plural(h.updates, 'update', 'updates') + '</span>' : '') + '</div></a>';
}

// --- the machine selected ---
function tools(h){
  const b = (m, text, cls, attrs) => '<button type=button class="btn btn--sm' + (cls ? ' ' + cls : '') + '" data-m=' + m + (attrs || '') + '>' + text + '</button>';
  return b('accept', h.accepting ? 'Close placement' : 'Open placement', '', ' data-on=' + (h.accepting ? 0 : 1))
    + b('ip', 'Public IP…') + b('rename', 'Rename…') + b('token', 'Rotate token…')
    + (Number(h.servers) ? '' : b('remove', 'Remove…', 'btn--danger')) + '<span class=spacer></span>'
    + (h.updates ? b('updall', 'Update all (' + h.updates + ')', 'btn--primary') : '');
}
function note(h){
  const total = Number(h.memory_mb) || 0, free = Number(h.free_mb) || 0;
  return [h.id, h.address, plural(Number(h.servers) || 0, 'hosted server', 'hosted servers'),
    total ? (free > 0 ? size(free) + ' free for new servers' : 'full') + ', ' + size(h.reserve_mb) + ' kept for itself, '
      + size(h.memory_used_mb) + ' in use' : '',
    !h.accepting ? 'closed: takes no new servers' : h.dns === 'off' ? 'open, but placement skips it while DNS is off'
      : 'open for new servers'].filter(Boolean).map(esc).join(' · ');
}
function nameCell(h, c){
  const names = new Set((h.containers || []).map(x => x.name)), s = c.server;
  let out = '<b class=mono>' + esc(c.name) + '</b>';
  if (c.self) out += ' <span class=pill title="the fleet agent: it reports this list and runs the jobs">agent</span>';
  if (c.kept) out += ' <span class=pill title="' + esc('kept stopped by a failed update; Roll back on ' + c.name.slice(0, -5) + ' brings it back') + '">kept</span>';
  if (c.orphan) out += ' <span class="pill pill--warn" title="no server row names it">orphan</span>';
  if (s) out += '<span class=sub><a href="#servers">hosted server ' + esc(s.label) + '</a> of ' + esc(s.username || '(deleted account)') + ' · ' + esc(s.state.replace(/_/g, '-')) + '</span>';
  else if (c.compose) out += '<span class=sub>' + esc([c.compose.project, c.compose.service].filter(Boolean).join(' / ')) + '</span>';
  if (names.has(c.name + '-prev') && !c.managed) out += '<span class=sub>a failed update kept ' + esc(c.name) + '-prev</span>';
  return out + (c.ports || []).map(p => '<span class="sub mono" title="a published port">' + esc(p) + '</span>').join('');
}
function imageCell(c){
  const id = String(c.image_id || '').replace(/^sha256:/, '').slice(0, 12);
  return '<span class=mono>' + esc(c.image || '?') + '</span>'
    + (c.image_stale ? ' <span class="pill pill--warn" title="the registry has a newer image for this tag">newer image</span>' : '')
    + (id ? '<span class="sub mono" title="' + esc(c.image_id) + '">' + esc(id) + '</span>' : '');
}
const HELPER = {container_update: 'updating', container_restart: 'restarting', container_rollback: 'rolling back'};
function statusCell(c){
  const bad = !c.kept && (DOWN.includes(c.status) || c.health === 'unhealthy');
  const cls = c.helper ? 'pill' : bad ? 'pill pill--warn' : c.status === 'running' ? 'pill pill--ok' : 'pill';
  const restarts = Number(c.restarts) || 0;
  return '<span class="' + cls + '">' + esc(c.status || 'unknown') + '</span>' + (c.health ? '<span class=sub>' + esc(c.health) + '</span>' : '')
    // the agent's own job only started a helper: the new agent says it is there with its first heartbeat
    + (c.helper ? '<span class=sub>' + (HELPER[c.helper] || 'replacing it') + ' through a helper; the new agent reports with its first heartbeat</span>' : '')
    + '<span class=sub title="Docker\'s restart count and the restart policy">' + plural(restarts, 'restart', 'restarts')
    + (c.restart_policy ? ' · ' + esc(c.restart_policy) : '') + '</span>';
}
// the agent's own container and its -prev: while one runs, starting the other would make a second agent
function twin(h, c){
  const own = (h.containers || []).find(x => x.self);
  return !!own && own.status === 'running' && (c.name === own.name + '-prev' || own.name === c.name + '-prev');
}
function useCell(c){
  if (c.status !== 'running') return '<span class=empty>—</span>';
  const mem = size(c.memory_mb) + (Number(c.memory_limit_mb) ? ' / ' + size(c.memory_limit_mb) : '');
  return (c.cpu_pct != null ? 'CPU ' + c.cpu_pct + ' %' : 'CPU unknown') + spark(c, 'cpu_pct', pct, 'CPU')
    + '<span class=sub>' + mem + spark(c, 'memory_mb', size, 'memory') + '</span>';
}
function actsCell(h, c){
  const btn = (m, text, cls) => '<button type=button class="btn btn--sm' + (cls ? ' ' + cls : '') + '" data-m=' + m + ' data-name="' + esc(c.name) + '">' + text + '</button>';
  if (c.orphan) return btn('orphan', 'Remove…', 'btn--danger') + ' ' + btn('logs', 'Logs');
  if (c.managed) return c.server ? '<a href="#servers">managed: see Servers</a>' : '<span class=empty>managed</span>';
  if (c.kept) return btn('logs', 'Logs');
  const prev = (h.containers || []).some(x => x.name === c.name + '-prev');
  return [btn('logs', 'Logs'), btn('restart', 'Restart…'), btn('update', 'Update…', c.image_stale ? 'btn--primary' : ''),
    UP.includes(c.status) ? (c.self ? '' : btn('stop', 'Stop…')) : twin(h, c) ? '' : btn('start', 'Start'),
    prev ? btn('rollback', 'Roll back…') : ''].filter(Boolean).join(' ');
}
function containerRow(h, c){
  const up = c.status === 'running' && c.started_at ? upFor(c.started_at) : '';
  return '<tr><td class=mname>' + nameCell(h, c) + '</td><td class=mimg>' + imageCell(c) + '</td><td>' + statusCell(c) + '</td>'
    + '<td class=nw>' + (up || '<span class=empty>—</span>') + (c.created_at ? '<span class=sub>made ' + ago(c.created_at) + '</span>' : '') + '</td>'
    + '<td class=nw>' + useCell(c) + '</td><td class=macts><span>' + actsCell(h, c) + '</span></td></tr>';
}
function containers(h){
  const cs = h.containers || [], own = cs.filter(c => !c.managed).sort(byName), hosted = cs.filter(c => c.managed).sort(byName);
  const group = (title, list) => list.length ? '<tr class=group><th colspan=6>' + title + '</th></tr>' + list.map(c => containerRow(h, c)).join('') : '';
  return group('The machine\'s own', own) + group('Hosted servers', hosted)
    || '<tr><td colspan=6 class=empty>' + (h.last_seen_at ? 'Its agent reports no containers (an agent before 0.3.0 does not list them).' : 'No heartbeat yet.') + '</td></tr>';
}
function jobRow(j){
  const full = resultText(j), short = full.length > 160 ? full.slice(0, 160) + '…' : full, id = esc(j.id);
  const btn = (m, text) => '<button type=button class="btn btn--sm" data-m=' + m + ' data-id="' + id + '" data-name="' + esc(j.label || '') + '">' + text + '</button> ';
  const kind = j.kind.startsWith('container_') ? j.kind.slice(10) : j.kind === 'update' ? 'environment' : j.kind;
  const wave = String(j.wave || '').match(/\/(\d+)$/);
  return '<tr><td>' + ago(j.created_at) + '</td><td>' + esc(kind) + (wave ? '<span class=sub>wave ' + Number(wave[1]) + '</span>' : '')
    + (j.attempts > 1 ? '<span class=sub>attempt ' + j.attempts + '</span>' : '') + '</td>'
    + '<td>' + esc(j.label || j.server_id || '') + (j.server_id ? '<span class=sub>hosted server</span>' : '') + '</td>'
    + '<td><span class="' + (JOB_PILL[j.state] || 'pill') + '">' + esc(j.state) + '</span></td><td>' + took(j) + '</td>'
    + '<td class=fres title="' + esc(full) + '">' + esc(short) + '</td><td>' + (j.kind === 'container_logs' && j.state === 'done' ? btn('view', 'View') : '')
    + (['failed', 'canceled'].includes(j.state) ? btn('retry', 'Retry') : '') + (['queued', 'held', 'failed'].includes(j.state) ? btn('cancel', 'Cancel') : '') + '</td></tr>';
}
function render(){
  if (!current()) selected = machines.length ? machines[0].id : '';
  const h = current();
  $('mnone').hidden = !loaded || machines.length > 0;
  $('mcards').innerHTML = machines.map(card).join('');
  $('msel').hidden = !h;
  live = !!h && (h.jobs || []).some(j => j.state === 'queued' || j.state === 'running');
  if (!h) return;
  const cs = h.containers || [];
  $('mhead').textContent = h.name; $('mpills').innerHTML = pills(h);
  $('mseen').textContent = live ? 'refreshing every 10 s' : '';
  $('mtools').innerHTML = tools(h);
  $('mnote').innerHTML = note(h);
  $('mtoken').hidden = tokenFor !== h.id;
  $('mchead').textContent = plural(cs.length, 'container', 'containers') + ' · ' + cs.filter(c => c.status === 'running').length + ' running'
    + (h.last_seen_at ? ' · as of the last heartbeat' : '');
  $('mrows').innerHTML = containers(h);
  $('mjobs').innerHTML = (h.jobs || []).map(jobRow).join('') || '<tr><td colspan=7 class=empty>No jobs on this machine yet.</td></tr>';
}

// --- loading, and the 10 s refresh while a job of the machine selected runs ---
const shown = () => !document.hidden && !TAB.hidden;
function schedule(){ clearTimeout(timer); timer = live && shown() ? setTimeout(() => { if (shown()) refresh(); }, 10000) : 0; }
async function refresh(){
  clearTimeout(timer); timer = 0;
  const seq = ++gen, err = $('merr');
  let d;
  try { d = await api('/api/admin/machines', undefined, 'GET'); }
  catch (e) {
    if (e.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
    if (seq === gen) { err.hidden = false; err.textContent = 'Could not load the machines: ' + e.message; schedule(); }
    return;
  }
  if (seq !== gen) return;
  err.hidden = true; machines = d.machines || []; loaded = true;
  render(); schedule();
}
document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(timer); timer = 0; } else if (live && shown()) refresh(); });

// After a job that restarts what serves this page: wait for the server to go and come back (or for the job to be done),
// then reload the page; five minutes at most.
async function comeBack(jobId, name){
  const n = $('mback'); n.hidden = false;
  n.textContent = name + ' restarts: this page is unreachable for a few seconds and reloads itself after.'; toast(n.textContent);
  let down = false;
  for (let i = 0; i < 150; i++) {
    await new Promise(r => setTimeout(r, 2000));
    let j;
    try { j = (await api('/api/admin/jobs/' + encodeURIComponent(jobId), undefined, 'GET')).job; }
    catch (e) { down = true; continue; }
    if (down || j.state === 'done') { location.reload(); return; }
    if (j.state === 'failed' || j.state === 'canceled') { n.textContent = name + ' was not restarted: ' + (resultText(j) || j.state) + '.'; return; }
  }
  n.hidden = true;
}

// --- actions ---
function showToken(r){
  tokenFor = r.host.id;
  $('mtokentext').textContent = 'GAMMA_FLEET_HOST_TOKEN=' + r.token + '\n\nShown once. On a fresh machine, as root, this installs the agent with it'
    + (r.host.public_ip ? ' and the machine\'s own Caddy' : '') + ':\n\n' + r.bootstrap
    + '\n\nOn a machine that runs the agent already, the token goes into its .env (cloud/fleet/deploy/README.md), then docker compose up -d.';
  $('mtoken').hidden = tokenFor !== selected;
}
const containerLogs = (h, name) => async () => {
  const r = await api(hostApi(h) + '/containers/' + encodeURIComponent(name) + '/logs', {lines: Number($('mlines').value) || 200});
  refresh(); return r.job;
};
// What happens when an update of a container this page is served through fails: the new one is stopped and the old one
// kept stopped, so the account server (or the way to it) stays down, and no job reaches the agent to roll it back.
const stuck = c => ' If the update fails, ' + c.name + ' stays down, and with it this page and every agent\'s way to its jobs: '
  + 'nothing here can roll it back. It is then started on the machine itself, with docker compose up -d in its Compose folder '
  + '(/root/Container/' + ((c.compose || {}).project || '…') + '), after pinning the previous image tag there if the new one is at fault.';
function confirmText(h, c, m){
  const back = ' This page is served through it: it is unreachable for a few seconds and reloads itself after.';
  if (m === 'stop') return front(h, c) ? 'Stop ' + c.name + '? This page, sign-in and every agent\'s jobs go through it: they stop with it, and only '
      + 'the machine itself can start it again (docker start ' + c.name + ').' : 'Stop ' + c.name + '? It stays stopped until you start it again here.';
  if (c.self) return m === 'update' ? 'Update the agent, ' + c.name + '? It starts a helper that pulls ' + c.image + ' and replaces it; the agent reports '
      + 'again within a few minutes, and jobs wait until then.' : 'Restart the agent, ' + c.name + '? It takes no jobs until it is back, a few seconds later.';
  const text = {restart: 'Restart ' + c.name + '? Docker stops it and starts it again.',
    update: 'Update ' + c.name + '? The agent pulls ' + c.image + ' and recreates the container with the same configuration; the old one is kept '
      + 'stopped as ' + c.name + '-prev until the new one runs.',
    rollback: 'Roll ' + c.name + ' back to ' + c.name + '-prev, the container its failed update kept? The live one is removed and the kept one '
      + 'starts in its place.'}[m];
  return text + (front(h, c) ? back + (m === 'update' ? stuck(c) : '') : '');
}
async function containerAction(b, h, c, m){
  const words = {restart: 'Restart', stop: 'Stop', update: 'Update', rollback: 'Roll back'};
  if (!await ask(confirmText(h, c, m), {ok: words[m], danger: m === 'stop' || m === 'rollback'})) return;
  act(b, async () => {
    const r = await api(hostApi(h) + '/containers/' + encodeURIComponent(c.name) + '/' + m, {});
    if (front(h, c) && m !== 'stop') comeBack(r.job.id, c.name);
    await refresh();
  });
}
TAB.addEventListener('click', async (ev) => {
  const b = ev.target.closest('button[data-m]'); if (!b) return;
  const h = current(), name = b.dataset.name, c = h && (h.containers || []).find(x => x.name === name);
  const go = fn => act(b, async () => { await fn(); await refresh(); });
  switch (b.dataset.m) {
    case 'addopen': b.hidden = true; $('madd').hidden = false; $('madd').elements.name.focus(); break;
    case 'addcancel': $('madd').reset(); $('madd').hidden = true; TAB.querySelector('[data-m=addopen]').hidden = false; break;
    case 'accept': go(() => api(hostApi(h), {accepting: b.dataset.on === '1'}, 'PATCH')); break;
    case 'ip': {
      const ip = await ask('The public IP of ' + h.name + '? With one, the machine runs its own Caddy and each server on it gets a DNS record; '
        + 'blank puts it behind the account server\'s Caddy.', {input: h.public_ip || '', blank: true, ok: 'Save'});
      if (ip !== null) go(() => api(hostApi(h), {public_ip: ip}, 'PATCH'));
      break;
    }
    case 'rename': {
      const n = await ask('A new name for ' + h.name + '?', {input: h.name, ok: 'Rename'});
      if (n && n !== h.name) go(() => api(hostApi(h), {name: n}, 'PATCH'));
      break;
    }
    case 'token': if (await ask('Give ' + h.name + ' a new agent token? The old one stops working at once: its agent is refused until the new '
        + 'token is in its .env.', {ok: 'Rotate', danger: true}))
      act(b, async () => { showToken(await api(hostApi(h) + '/token', {})); b.disabled = false; }); break;
    case 'remove': if (await ask('Remove ' + h.name + '? Its token stops working and its jobs that have not finished are canceled. Nothing on '
        + 'the machine itself is touched: stop its agent there.', {ok: 'Remove', danger: true}))
      act(b, async () => { await api(hostApi(h), undefined, 'DELETE'); selected = ''; history.replaceState(null, '', '#machines'); await refresh(); });
      break;
    case 'updall': {
      const todo = updatable(h), fr = todo.find(x => front(h, x));
      if (!await ask('Update ' + plural(todo.length, 'container', 'containers') + ' on ' + h.name + ' to the newest image of its tag: '
          + todo.map(x => x.name + (x.self ? ' (the agent, last, once the others are done)' : '')).join(', ')
          + '? Each is recreated with the same configuration.'
          + (fr ? ' ' + fr.name + ' serves this page: it is unreachable for a few seconds and reloads itself after.' + stuck(fr) : ''),
          {ok: 'Update all'})) break;
      act(b, async () => {
        const r = await api(hostApi(h) + '/update-all', {}), j = fr && (r.jobs || []).find(x => x.label === fr.name);
        if (j) comeBack(j.id, fr.name);
        await refresh();
      });
      break;
    }
    case 'tokendone': tokenFor = ''; $('mtoken').hidden = true; break;
    case 'logs': logs.show(name + ' on ' + h.name, containerLogs(h, name)); break;
    case 'view': logs.watch(b.dataset.id, name + ' on ' + h.name, containerLogs(h, name)); break;
    case 'logagain': logs.again(); break;
    case 'logclose': logs.close(); break;
    case 'start': go(() => api(hostApi(h) + '/containers/' + encodeURIComponent(name) + '/start', {})); break;
    case 'restart': case 'stop': case 'update': case 'rollback': if (c) containerAction(b, h, c, b.dataset.m); break;
    case 'orphan': if (c && await ask('Remove the container ' + name + '? No server row names it; the agent removes the container and its data directory.',
        {ok: 'Remove', danger: true}))
      go(() => api(hostApi(h) + '/orphans/' + encodeURIComponent(c.label) + '/remove', {})); break;
    case 'retry': case 'cancel': go(() => api('/api/admin/jobs/' + encodeURIComponent(b.dataset.id) + '/' + b.dataset.m, {})); break;
  }
});
$('mlines').onchange = () => logs.again();
bind('madd', async (d) => {
  const r = await api('/api/admin/hosts', {name: d.name, address: d.address, public_ip: d.public_ip});
  $('madd').reset(); $('madd').hidden = true; TAB.querySelector('[data-m=addopen]').hidden = false;
  selected = r.host.id; history.pushState(null, '', '#machines/' + r.host.id);
  showToken(r); await refresh();
});
// the tab's entry, with the machine #machines/<host id> names (none: the one selected before, else the first)
async function load(id){ if (id) selected = id; if (loaded) render(); return refresh(); }
function pick(id){ selected = id; render(); schedule(); }
return [load, pick];
})();
"""

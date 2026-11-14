// An agent's ask on this computer (docs/architecture.md "An agent's ask on
// this computer"; docs/dev/local_agents.md "Asking on this computer"): a
// turn of a chat on another server's page, run here by the host for a work
// folder whose agent may ask on this computer, asks to run a command or to
// change files outside its sandbox, and the question is drawn and answered
// in the bar alone. The host lists what waits (`GET /api/local-agents/asks`,
// the shell's word): `{id, turn_id, link_id, agent, label, kind, command,
// lines?, inner?, shell?, marked?, background?, dir, folder, file, host?,
// paths?, diff?, reason?, server: {origin, account}, at, expires_at}`, every
// string as Gamma marks what would not show. The queue, the card's words and
// what the shell says around it are made here; main.js follows the host and
// arms the buttons, and the bar draws what comes back as plain text. Pure:
// no Electron (test/agentAsks.test.js, `npm test`).

// Every character that would not show, as Gamma's own reading marks it
// (gamma/local_agents/events.py shown_text): each Cc but the newline and the
// tab, each Cf (bidi controls and isolates, zero-width, the BOM, U+2060…),
// and U+2028/9 (Zl and Zp, which hold nothing else), drawn `⟨U+202E⟩`. The
// host marks them already; a string marked here again is unchanged, and
// one it missed is marked all the same.
const UNSEEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;
function shown(text) {
  let n = 0;
  const out = String(text == null ? '' : text).replace(UNSEEN, (ch) => {
    if (ch === '\n' || ch === '\t') return ch;
    n += 1;
    return `⟨U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}⟩`;
  });
  return [out, n];
}

const oneLine = (text, max) => String(text || '').replace(/\s+/g, ' ').trim().slice(0, max);
const baseName = (p) => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || String(p || '');

const originOf = (url) => {
  try { return new URL(url).origin; } catch { return ''; }
};
const hostOf = (url) => {
  try { return new URL(url).host; } catch { return String(url || ''); }
};

// A time as the card says it: hours and minutes, local time.
function clock(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// The queue after a reading `list` of the host: the asks not answered here
// (`tombstones`, the ids main.js answered, which a list read before the
// answer may still hold), those already queued kept in their place, the
// new ones behind them in the order they were asked (`at`). A tombstone
// whose ask the list no longer holds is dropped from the set.
function take(queue, list, tombstones) {
  const listed = new Map();
  for (const a of Array.isArray(list) ? list : []) if (a && typeof a.id === 'string' && a.id) listed.set(a.id, a);
  for (const id of [...tombstones]) if (!listed.has(id)) tombstones.delete(id);
  const kept = (queue || []).filter((a) => listed.has(a.id) && !tombstones.has(a.id)).map((a) => ({ ...listed.get(a.id), hostId: a.hostId }));
  const have = new Set(kept.map((a) => a.id));
  const fresh = [...listed.values()].filter((a) => !have.has(a.id) && !tombstones.has(a.id))
    .sort((x, y) => String(x.at || '').localeCompare(String(y.at || '')));
  return [...kept, ...fresh];
}

// The server an ask comes from, as the card names it: the registry's
// remote server at its origin, else the origin's host; the account there,
// 64 characters at most.
function fromOf(ask, servers) {
  const s = ask.server || {};
  const origin = originOf(s.origin) || shown(s.origin)[0];
  const known = (servers || []).find((x) => x.type === 'remote' && originOf(x.url) === origin);
  return { name: shown(known ? known.name : hostOf(origin))[0], origin, account: shown(oneLine(s.account, 64))[0] };
}

const label = (ask) => shown(oneLine(ask.label, 40) || 'The agent')[0];

// The first of the queue as the bar draws it, or null: `key` (the ask's id),
// `kind`, `label`, `hostId` (the local server it came from, as main.js put it
// on the ask), `link` (the work folder's link there), `waiting` (the
// queue's length) and `from`
// (`{name, origin, account}`), and the card's words: `caption`, `count` ("1
// of 2 waiting"), `title`, `lines` ("3 lines"), `box` (the command, or the
// inner command of one shell wrapper), `through`, `rows` (`[label, value,
// mono]`: In, Network, Files, From, As), `diff`, `says` (the agent's
// reason, marked as the agent's), `notes` (the marks, the background, the
// risk, a replaced card, the time it is declined by itself), `stop` (the
// footer's link), `folder` (the directory's name, for a notification) and
// `marked` (how many characters Gamma marked: the bar draws ⟨U+…⟩ as a mark
// only then, since the agent may write the same letters itself).
// `replaced`: the id of an ask main.js shows in place of one that went
// away unanswered here.
function cardOf(queue, servers, replaced = '') {
  const ask = (queue || [])[0];
  if (!ask) return null;
  const name = label(ask);
  const from = fromOf(ask, servers);
  const files = ask.kind === 'files';
  let marks = Number.isInteger(ask.marked) ? ask.marked : 0;
  const mark = (text) => {
    const [out, n] = shown(text);
    marks += n;
    return out;
  };
  const command = mark(ask.command);
  const inner = ask.inner && ask.shell ? mark(ask.inner) : '';
  const lines = Number.isInteger(ask.lines) && ask.lines > 1 ? ask.lines : 0;
  const paths = files ? (Array.isArray(ask.paths) ? ask.paths : []).map(mark) : [];
  const file = mark(ask.file);
  const reason = mark(oneLine(ask.reason, 300));
  const rows = files
    ? [['Files', paths.join('\n'), true]]
    : [['In', mark(ask.dir), true], ...(ask.host ? [['Network', mark(ask.host), false]] : [])];
  const named = from.name && from.name !== hostOf(from.origin) ? `${from.name} (${from.origin})` : from.origin;
  rows.push(['From', file ? `the chat of ${file} on ${named}` : `the chat on ${named}`, false]);
  if (from.account) rows.push(['As', from.account, false]);
  const at = clock(ask.expires_at);
  const notes = [
    marks ? `This holds ${marks === 1 ? '1 character that does' : `${marks} characters that do`} not show: Gamma marks ${marks === 1 ? 'it' : 'them'} ⟨U+…⟩.` : '',
    ask.background && !files ? 'Asked to run in the background: Gamma runs it in the foreground, so Stop ends it.' : '',
    files ? 'It changes files outside the work folder.'
      : `It runs as you, with your files and the network. A build also runs what this folder's files say, and ${from.name} can change them.`,
    replaced && replaced === ask.id ? 'The question before this one went away: this is another one.' : '',
    at ? `Declined by itself at ${at} unless you answer.` : '',
  ].filter(Boolean);
  const waiting = queue.length;
  return {
    key: ask.id, kind: files ? 'files' : 'command', label: name, hostId: ask.hostId || '', link: ask.link_id || '', waiting, from,
    caption: 'Approval needed on this computer', count: waiting > 1 ? `1 of ${waiting} waiting` : '',
    title: files ? `${name} wants to change files outside its sandbox` : `${name} wants to run a command`,
    lines: !files && lines ? `${lines} lines` : '', box: files ? '' : inner || command,
    through: !files && inner ? `Through ${mark(ask.shell)}: ${command}` : '',
    rows, diff: files ? mark(ask.diff) : '', says: reason ? `${name} says: “${reason}”` : '', notes,
    stop: `Stop ${name} asking from ${from.name}`, folder: mark(baseName(ask.folder)), expires: at,
    marked: marks,
  };
}

// What the bar says once an answer was taken (`decision`: once, deny or
// stop), and around it.
function notice(card, decision) {
  if (decision === 'once') return `Allowed once: ${card.label} goes on.`;
  if (decision === 'stop') return `Declined, and ${card.label} no longer asks on this computer for ${card.from.name}.`;
  return `Declined: ${card.label} goes on without it.`;
}
const stale = (card) => `${card.label} no longer waits for that: nothing was allowed.`;
const unsure = (card) => `Gamma did not hear whether ${card.label} got your answer: see the chat on ${card.from.name}.`;
const couldNot = (card, message) => `Could not answer ${card.label}: ${String(message || 'no answer').replace(/\.$/, '')}.` +
  (card.expires ? ` Without an answer it is declined at ${card.expires}.` : ' Without an answer it is declined.');
const answerFirst = (card) => `Answer ${card.label}'s question first.`;

// The OS notification for a card, the shell's words only: the folder's
// name, never its path (a toast can show on a locked screen).
function notification(card) {
  return { title: card.kind === 'files' ? `${card.label} asks to change files` : `${card.label} asks to run a command`,
    body: `From ${card.from.name}, in ${card.folder}. Answer it in Gamma's window.` };
}

// The tray's first line while asks wait, and its tooltip; '' for none.
function trayLabel(queue) {
  if (!queue || !queue.length) return '';
  return queue.length > 1 ? `Answer ${label(queue[0])} (${queue.length} waiting)…` : `Answer ${label(queue[0])}…`;
}
const trayTip = (queue) => (queue && queue.length ? `Gamma: ${label(queue[0])} waits for your answer` : 'Gamma');

module.exports = { shown, take, cardOf, notice, stale, unsure, couldNot, answerFirst, notification, trayLabel, trayTip };

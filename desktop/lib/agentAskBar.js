// A turn of a chat on another server's page, which the host runs for a
// work folder while its agent may ask (setAgentRuns 'ask'), asks to run a
// command or to change files outside its
// sandbox (docs/architecture.md "An agent's ask on this computer";
// docs/dev/local_agents.md "Asking on this computer"). The question is the
// host's and is drawn here alone, in the bar, over the whole window with
// the content view hidden: the page that started the turn is neither seen
// nor clicked, and nothing that server sends draws, words or answers it.
// A co-author's turn on the host's own server asks here too, beside its
// own chat (docs/dev/local_agents.md "A co-author's turn"). One follower
// holds `GET /api/local-agents/asks` on the host, with the shell's word,
// while the host keeps such a folder, or its agent serves a co-author of
// it, and an agent may ask (refreshKeeping); the
// queue (lib/agentAsks.js take) keeps the card shown until it is answered
// or goes, the asks that come meanwhile behind it. A new card disarms the
// buttons, which arm after a quiet, focused second (startArming), and an
// answer counts only for a press made after that (barInput); it goes to
// the host's `POST /api/local-agents/asks/<id>` with the shell's word, and
// the bar says only what that answered. A window hidden, minimized or
// closed comes back without the focus, flashed, with a notification in the
// shell's words (surfaceForAsk); the tray's first line says what waits.

const { app, Notification } = require('electron');
const registry = require('./registry');
const sidecar = require('./sidecar');
const keeping = require('./keeping');
const agentAsks = require('./agentAsks');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

const ASK_HOLD_S = 20; // how long the host holds a read that has nothing new
const ASK_RETRY_MS = 2_000;
const ARM_MS = 1_000;
const SURFACE_MS = 60_000;
let askQueue = []; // the asks waiting, the shown one first (each with `hostId`, the local server it came from)
const askTombstones = new Set(); // the ids answered here, kept out of a list read before the answer
const unsureAsks = new Set(); // the ids of the asks an answer went to without word of whether it was taken (one each per 30 s timeout, until answered again)
let askVersion = -1; // the host's version of its list, the next read's `after` (-1: none yet, answered at once)
let askFollower = null; // { server, ctrl } while one follows the host
let shownKey = ''; // the id of the ask the bar shows, '' for none
let replacedKey = ''; // the shown ask, when it took the place of one that went away unanswered here
let answeredKey = ''; // the last ask answered here
let drawnKey = ''; // the ask the bar said it drew (shell:agent-ask-drawn)
let armed = false;
let armTimer = null;
let arming = 0; // counts the armings; `pressedIn`, the arming a press after it came in
let pressedIn = -1;
let contentTitle = ''; // the page's title, back once the card goes
let surfacedAt = 0; // when a card last brought the window
let focusedSince = true; // the window had the focus since then
const toasts = new Set(); // the notifications up, kept from the collector until they close
// under GAMMA_SHELL_TEST: what was surfaced, the notifications and the answers sent
const surfaced = [];
const notifications = [];
const askAnswers = [];

const pause = (ms) => new Promise((done) => setTimeout(done, ms));

// The card the bar draws, or null.
function askCard() {
  return agentAsks.cardOf(askQueue, registry.load().servers, replacedKey);
}

// The card as the shell state carries it, with whether its buttons are armed.
function agentAskState() {
  const card = askCard();
  return card && { ...card, armed };
}

// The asks of local server `serverId` followed (a no-op while they are):
// each read held by the host until its list changes, up to ASK_HOLD_S. A
// failure empties the queue, so nothing shown can be answered: another try
// after ASK_RETRY_MS while the server runs, none for a 404 (a server that
// runs no agents) or a 403 (the word refused: a bug, logged), which the
// next keeping reading starts again.
function followAsks(serverId) {
  if (askFollower && askFollower.server === serverId) return;
  stopAsks();
  const follower = { server: serverId, ctrl: new AbortController() };
  askFollower = follower;
  (async () => {
    while (askFollower === follower) {
      const after = askVersion;
      const t0 = Date.now();
      let answer;
      try {
        const { origin, api } = await ctx.localServer(serverId);
        answer = await api(origin, `/api/local-agents/asks?after=${after}&wait=${ASK_HOLD_S}`, {
          headers: ctx.shellWord(origin), signal: AbortSignal.any([follower.ctrl.signal, AbortSignal.timeout((ASK_HOLD_S + 10) * 1000)]),
        });
      } catch (e) {
        if (askFollower !== follower) return;
        dropAsks();
        if (e.status === 403) console.error(`[shell] the host refused the shell's word for its asks: ${e.message}`);
        if (e.status === 404 || e.status === 403 || !sidecar.status(serverId)) {
          askFollower = null;
          return;
        }
        await pause(ASK_RETRY_MS);
        continue;
      }
      if (askFollower !== follower) return;
      // (every list is taken: the tombstones keep an answered ask out of one
      // read before the answer, and a host started again counts from 0)
      askVersion = Number.isInteger(answer.version) ? answer.version : 0;
      askQueue = agentAsks.take(askQueue, (Array.isArray(answer.asks) ? answer.asks : []).map((a) => ({ ...a, hostId: serverId })), askTombstones);
      asksChanged();
      // a host that answered at once with nothing new does not hold the read
      if (askVersion === after && Date.now() - t0 < 1000) await pause(ASK_RETRY_MS);
    }
  })().catch((e) => console.error(`[shell] following the asks: ${(e && e.message) || e}`));
}

function stopAsks() {
  if (askFollower) {
    askFollower.ctrl.abort();
    askFollower = null;
  }
  dropAsks();
}

function dropAsks() {
  askVersion = -1;
  askTombstones.clear();
  if (!askQueue.length && !shownKey) return;
  askQueue = [];
  asksChanged();
}

// The queue changed: a card whose ask is new is disarmed (a replacement of
// one that went away unanswered here says so) and brings the window; no
// card, the bar goes back to its strip and the page shows again.
function asksChanged() {
  const first = askQueue[0];
  if (first && first.id !== shownKey) {
    replacedKey = shownKey && shownKey !== answeredKey ? first.id : '';
    shownKey = first.id;
    drawnKey = '';
    disarm();
    surfaceForAsk(askCard());
  } else if (!first && shownKey) {
    shownKey = '';
    replacedKey = '';
    drawnKey = '';
    disarm();
    if (ctx.win && !ctx.win.isDestroyed()) ctx.win.setTitle(contentTitle || 'Gamma');
  }
  if (shownKey && ctx.win && !ctx.win.isDestroyed()) ctx.win.setTitle('Gamma');
  ctx.setBarExpanded(ctx.barAsked);
  ctx.pushState();
  if (ctx.tray) {
    ctx.tray.setContextMenu(ctx.trayMenu());
    ctx.tray.setToolTip(agentAsks.trayTip(askQueue));
  }
}

function disarm() {
  armed = false;
  clearTimeout(armTimer);
  armTimer = null;
}

// The buttons of the card shown armed after ARM_MS, once the bar drew it
// and while the window has the focus (the test scripts' minimized window
// never has it: taken as given there); counted again from now.
function startArming() {
  disarm();
  if (!shownKey || drawnKey !== shownKey || !ctx.win || ctx.win.isDestroyed()) return;
  if (!ctx.TEST_MINIMIZED && !ctx.win.isFocused()) return;
  armTimer = setTimeout(() => {
    armTimer = null;
    armed = true;
    arming += 1;
    ctx.pushState();
  }, ARM_MS);
}

// The bar drew the card `key`: its quiet second starts.
function agentAskDrawn(key) {
  if (!key || key !== shownKey) return false;
  if (drawnKey === key) return true;
  drawnKey = key;
  startArming();
  return true;
}

// A press, a key, a wheel or a touch in the bar while a card is up (Chromium
// names a key pressed `rawKeyDown`, its repeat as typed `keyDown`; a tap
// reaches it as `touchStart`, the mouse events and the click for it are
// made in the page).
const PRESSES = ['mouseDown', 'rawKeyDown', 'keyDown', 'mouseWheel', 'touchStart'];
function barInput(type) {
  if (!shownKey || !PRESSES.includes(type)) return;
  if (armed) pressedIn = arming;
  else startArming();
}

// The window's events while a card may be up (main.js createWindow). A
// window made or closed draws the card again, armed afresh once its bar
// drew it.
function redrawCard() {
  disarm();
  drawnKey = '';
}

function windowFocused() {
  focusedSince = true;
  try { ctx.win.flashFrame(false); } catch {}
  rearm();
}

// A focus, a resize or a move counts the quiet second again.
function rearm() {
  if (shownKey) startArming();
}

function windowBlurred() {
  if (!armed && !armTimer) return;
  disarm();
  ctx.pushState();
}

// The page's title, which the window takes but while a card hides the page.
function pageTitled(title) {
  contentTitle = title || '';
  if (ctx.win && !shownKey) ctx.win.setTitle(title || 'Gamma');
}

// main.test.js: a list as the follower would read it from local server
// `serverId` (the host by default), and the window not brought yet.
function pretendAsks(list, serverId = (registry.load().servers.find((s) => s.type === 'local') || {}).id || '') {
  askQueue = agentAsks.take(askQueue, list.map((a) => ({ ...a, hostId: serverId })), askTombstones);
  asksChanged();
  return agentAskState();
}

function forgetSurfaced() {
  surfacedAt = 0;
  focusedSince = true;
}

// The bar's Allow once, Don't allow or Stop asking (`decision` once, deny or
// stop) on the card `key`: only the card shown, armed, after a press made
// since. Every button goes inactive at once; then the host's answer route
// with the shell's word (stop declines), and for stop, once the decline is
// written, the folder's policy back to never. The bar says what the route
// answered: taken, gone meanwhile (404, 409; unknown still after an earlier
// answer left unknown, which may be the one taken), not reached (the card
// stays, armed again), or unknown after the request left (a timeout, a
// reset).
async function answerAgentAsk(key, decision) {
  const ask = askQueue[0];
  if (!ask || key !== shownKey || ask.id !== key || !armed || pressedIn !== arming || !['once', 'deny', 'stop'].includes(decision)) return false;
  const card = askCard();
  disarm();
  ctx.pushState();
  if (process.env.GAMMA_SHELL_TEST) askAnswers.push({ id: ask.id, decision });
  let left = false;
  let said = '';
  let taken = false;
  try {
    const { origin, api } = await ctx.localServer(ask.hostId);
    const headers = { ...ctx.JSON_TYPE, ...ctx.shellWord(origin) };
    left = true;
    const r = await api(origin, `/api/local-agents/asks/${encodeURIComponent(ask.id)}`, {
      method: 'POST', headers, body: JSON.stringify({ decision: decision === 'stop' ? 'deny' : decision }), signal: AbortSignal.timeout(30_000),
    });
    if (Number.isInteger(r && r.version)) askVersion = r.version;
    taken = true;
    said = agentAsks.notice(card, decision);
  } catch (e) {
    const why = `${(e && e.message) || ''} ${(e && e.cause && e.cause.message) || ''}`;
    if (e && (e.status === 404 || e.status === 409)) {
      taken = true;
      // an earlier answer to it may be the one taken: "nothing was allowed" is said only when none may be
      said = unsureAsks.has(ask.id) ? agentAsks.unsure(card) : agentAsks.stale(card);
    } else if (!left || (e && e.status) || /ERR_CONNECTION_REFUSED|ECONNREFUSED/.test(why)) {
      said = agentAsks.couldNot(card, (e && e.message) || String(e));
    } else {
      said = agentAsks.unsure(card);
      unsureAsks.add(ask.id);
    }
  }
  if (taken) {
    unsureAsks.delete(ask.id);
    askTombstones.add(ask.id);
    askQueue = askQueue.filter((a) => a.id !== ask.id);
    answeredKey = ask.id;
    asksChanged();
    // Stop asking: the agent no longer asks, the safe way, whatever became of the ask
    if (decision === 'stop' && Object.prototype.hasOwnProperty.call(keeping.AGENT_LABELS, ask.agent)) {
      try {
        const { origin, api } = await ctx.localServer(ask.hostId);
        await api(origin, `/api/local-agents/${ask.agent}`, {
          method: 'PATCH', headers: ctx.JSON_TYPE, body: JSON.stringify({ runs: 'never' }),
        });
      } catch (e) {
        said += ` Could not stop ${card.label} asking: ${String((e && e.message) || e).replace(/\.$/, '')}.`;
      }
      ctx.refreshKeeping().catch(() => {});
    }
  } else {
    startArming();
    ctx.pushState();
  }
  ctx.showNotice(said);
  return true;
}

// A new card brings the window: made on the launcher when there is none
// (bundled, no network), shown when hidden or minimized, never focused, so
// that every card can be answered; when it has not the focus, flashed, the
// Dock bouncing on macOS, and a notification in the shell's words (its
// click shows the window), these at most once a minute while the window
// has not had the focus since.
function surfaceForAsk(card) {
  if (!card) return;
  if (process.env.GAMMA_SHELL_TEST) surfaced.push(card.key);
  if (ctx.TEST_MINIMIZED) return;
  if (!ctx.win || ctx.win.isDestroyed()) {
    ctx.createWindow({ inactive: true });
    ctx.loadLauncher();
  } else if (!ctx.win.isVisible() || ctx.win.isMinimized()) {
    ctx.win.showInactive();
  }
  if (ctx.win.isFocused()) return;
  const now = Date.now();
  if (surfacedAt && now - surfacedAt < SURFACE_MS && !focusedSince) return;
  surfacedAt = now;
  focusedSince = false;
  try { ctx.win.flashFrame(true); } catch {}
  if (process.platform === 'darwin' && app.dock && app.dock.bounce) app.dock.bounce('informational');
  const words = agentAsks.notification(card);
  if (process.env.GAMMA_SHELL_TEST) {
    notifications.push(words);
    return;
  }
  if (!Notification.isSupported()) return;
  const n = new Notification({ title: words.title, body: words.body });
  toasts.add(n);
  n.on('click', () => ctx.showWindow());
  n.on('close', () => toasts.delete(n));
  n.show();
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  followAsks,
  stopAsks,
  get askQueue() { return askQueue; },
  get askFollower() { return askFollower; },
  get shownKey() { return shownKey; },
  surfaced,
  notifications,
  askAnswers,
  askCard,
  agentAskState,
  agentAskDrawn,
  barInput,
  redrawCard,
  windowFocused,
  rearm,
  windowBlurred,
  pageTitled,
  pretendAsks,
  forgetSurfaced,
  answerAgentAsk,
};

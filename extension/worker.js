// Service worker: per-tab detection state + toolbar badge, the save pipeline
// (thin — one POST /api/clip does the ingest server-side), context menus, the
// keyboard command, the popup's message API, and the tabs a chat handed a
// blocked fetch to. State lives in chrome.storage.session so it survives the
// worker being put to sleep.

import { api, ApiError, getSettings, serverOrigin, whoAmI } from "./api.js";
import {
  NEEDS_YOU, backgroundBusy, checkPage, handoffIdFrom, harvestUrls, needsSignIn, nextToOpen, sameWork, siteOf,
} from "./handoff.js";
import { connectPublisher, publisherHost, publisherRoot, secureServer, shouldAutoRefresh } from "./publisherSessions.js";
import "./ids.js"; // defines globalThis.gammaDoiFromPath and gammaArxivId

const ICON_ON = { 16: "assets/icons/icon16.png", 32: "assets/icons/icon32.png" };
const ICON_OFF = { 16: "assets/icons/icon16-off.png", 32: "assets/icons/icon32-off.png" };
const COLORS = { accent: "#3a7bd5", ok: "#2e8b5e", danger: "#c94a4a", muted: "#7a828e" };

// ---------- per-tab state ----------

const key = (tabId) => `tab:${tabId}`;

async function getTabState(tabId) {
  const r = await chrome.storage.session.get(key(tabId));
  return r[key(tabId)] || {};
}

async function setTabState(tabId, patch) {
  const next = { ...(await getTabState(tabId)), ...patch };
  await chrome.storage.session.set({ [key(tabId)]: next });
  updateBadge(tabId, next).catch(() => {});
  return next;
}

async function updateBadge(tabId, st) {
  const kind = st.candidate && st.candidate.kind || "none";
  let text = "", color = COLORS.accent, on = false;
  if (st.auth === false) { text = "!"; color = COLORS.danger; }
  else if (st.hit) { text = "✓"; color = COLORS.ok; on = true; }
  else if (kind === "pdf") { text = "PDF"; on = true; }
  else if (kind === "arxiv") { text = "arX"; on = true; }
  else if (kind === "doi") { text = "DOI"; on = true; }
  else if (kind === "maybe") { text = "?"; color = COLORS.muted; on = true; }
  try {
    await chrome.action.setBadgeText({ tabId, text });
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setIcon({ tabId, path: on ? ICON_ON : ICON_OFF });
  } catch {}
}

// A DOI used as a URL path (the rule lives in ids.js, shared with detect.js).
function doiFromUrl(url) {
  try { return globalThis.gammaDoiFromPath(new URL(url).pathname); } catch { return ""; }
}

// Tabs without a content script (Chrome's PDF viewer, restricted pages):
// what the URL alone tells us.
function candidateFromUrl(url, title) {
  if (!url || !/^https?:/i.test(url)) return { kind: "none", source_url: url || "" };
  const isPdf = /\.pdf($|[?#])/i.test(url.split("?")[0]);
  const doi = doiFromUrl(url);
  const arxivId = globalThis.gammaArxivId(url);
  const isArxivPdf = /arxiv\.org\/pdf\//i.test(url);
  const pdfUrl = isPdf || isArxivPdf ? url : "";
  const kind = pdfUrl ? "pdf" : arxivId ? "arxiv" : doi ? "doi" : "none";
  let cleanTitle = (title || "").replace(/\s+/g, " ").trim();
  // Chrome titles PDF tabs with their URL or filename — no better than the
  // server's own fallback, and the metadata lookup replaces it anyway.
  if (cleanTitle && (url.includes(cleanTitle) || /\.pdf$/i.test(cleanTitle))) cleanTitle = "";
  return { kind, source_url: url, pdf_url: pdfUrl, arxiv_id: arxivId, doi,
           title: cleanTitle, is_pdf_tab: isPdf || isArxivPdf, from_url: true };
}

// Content-script detection wins over the URL guess, field by field.
function mergeCandidates(fromPage, fromUrl) {
  if (!fromPage) return fromUrl;
  if (!fromUrl) return fromPage;
  const c = { ...fromUrl, ...fromPage, from_url: false };
  for (const k of ["pdf_url", "arxiv_id", "doi", "title"]) if (!c[k] && fromUrl[k]) c[k] = fromUrl[k];
  if (c.kind === "none" && fromUrl.kind !== "none") c.kind = fromUrl.kind;
  c.is_pdf_tab = !!(fromPage.is_pdf_tab || fromUrl.is_pdf_tab);
  return c;
}

// ---------- auth + lookup ----------

let authCache = { at: 0, value: null };

async function checkAuth(force = false) {
  const origin = await serverOrigin();
  if (!force && Date.now() - authCache.at < 60_000 && authCache.value?.origin === origin) return authCache.value;
  let value;
  if (!origin) value = { configured: false, auth: null, user: null, origin: "" };
  else {
    try {
      const me = await whoAmI();
      value = { configured: true, auth: !!me.user, user: me.user, origin: me.origin, is_guest: !!me.is_guest };
    } catch (err) {
      value = { configured: true, auth: null, user: null, origin, error: err.message };
    }
  }
  authCache = { at: Date.now(), value };
  return value;
}

async function lookup(candidate, origin) {
  if (!candidate || candidate.kind === "none") return { hit: null };
  try {
    const hit = await api("/library/lookup", {
      expectedOrigin: origin,
      params: { doi: candidate.doi, arxiv_id: candidate.arxiv_id, url: candidate.pdf_url || candidate.source_url },
    });
    return { hit };
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return { hit: null };
    if (err instanceof ApiError && err.status === 401) return { hit: null, auth: false };
    return { hit: null, error: err.message };
  }
}

// The registry record (title, authors, year, venue) behind a detected
// identifier — a PDF tab has no meta tags, so this is where its title comes
// from before the paper is saved. Public data, so the server caches it.
async function preview(candidate) {
  if (!candidate || !(candidate.doi || candidate.arxiv_id)) return null;
  try { return await api("/library/preview", { params: { doi: candidate.doi, arxiv_id: candidate.arxiv_id } }); }
  catch { return null; }
}

async function setDetection(tabId, candidate) {
  const st = await setTabState(tabId, { candidate, hit: null, preview: null, looked: false });
  const auth = await checkAuth();
  if (!auth.configured) return st;
  if (auth.auth === false) return setTabState(tabId, { auth: false });
  const res = await lookup(candidate, auth.origin);
  if (await serverOrigin() !== auth.origin) return getTabState(tabId);
  if (res.auth === false) { authCache.at = 0; return setTabState(tabId, { auth: false, looked: true }); }
  const next = await setTabState(tabId, { hit: res.hit, auth: true, looked: true, origin: auth.origin });
  // Off the badge's critical path: doi.org can take a second or two. The
  // popup re-renders its head when the record lands (storage.onChanged).
  preview(candidate).then(async (pv) => {
    if (!pv) return;
    const cur = await getTabState(tabId);
    if (cur.candidate === undefined || cur.candidate.source_url !== candidate.source_url) return;  // tab moved on
    await setTabState(tabId, { preview: pv });
  }).catch(() => {});
  return next;
}

// ---------- publisher sessions ----------

// GET /publisher-sessions (connection metadata, never cookie values), cached
// briefly so a page load on a journal site costs no request most of the
// time. `supported: false` = guest, or a server without the endpoint.
const PUBLISHER_TTL = 5 * 60_000;
const AUTO_KEY = "publisher:auto";         // last automatic refresh → {host, at, ok, error}
const ATTEMPTS_KEY = "publisher:attempts"; // host → epoch seconds of the last automatic try
let publisherCache = null;

async function publisherStatus(force = false) {
  const auth = await checkAuth();
  if (!auth.configured || !auth.auth || auth.is_guest || auth.user === "guest") return { supported: false };
  const fresh = publisherCache && Date.now() - publisherCache.at < PUBLISHER_TTL
    && publisherCache.user === auth.user && publisherCache.origin === auth.origin;
  if (!force && fresh) return publisherCache;
  const base = { at: Date.now(), user: auth.user, origin: auth.origin, secure: secureServer(auth.origin) };
  try {
    const data = await api("/publisher-sessions", { expectedUser: auth.user, expectedOrigin: auth.origin });
    publisherCache = { ...base, supported: true, roots: data.publisher_roots || [], sessions: data.sessions || [] };
  } catch (err) {
    if (err.status === 404) publisherCache = { ...base, supported: false };  // older server
    else throw err;
  }
  return publisherCache;
}

// Called on every https tab load. Re-imports the cookies of a host the user
// already connected, when the server's snapshot has gone stale
// (publisherSessions.shouldAutoRefresh) — never a host that was not connected
// by hand, never without the optional cookies permission, never a prompt.
// `force` (a chat's fetch just succeeded in this tab, so the browser's
// cookies are fresh and working) skips the age and retry checks.
async function autoRefreshPublisher(tabId, url, { force = false } = {}) {
  const { autoRefreshSessions } = await getSettings();
  if (!autoRefreshSessions) return;
  if (!(await chrome.permissions.contains({ permissions: ["cookies"] }))) return;
  // The roots list is stable: a page off every known publisher costs nothing.
  if (publisherCache && publisherCache.roots && !publisherHost(url, publisherCache.roots)) return;
  const status = await publisherStatus();
  if (!status.supported || !status.secure) return;
  const host = publisherHost(url, status.roots);
  if (!host) return;
  const session = status.sessions.find((s) => s.host === host);
  const attempts = (await chrome.storage.session.get(ATTEMPTS_KEY))[ATTEMPTS_KEY] || {};
  const now = Date.now() / 1000;
  if (!session || (!force && !shouldAutoRefresh({ session, attempts, now }))) return;
  attempts[host] = now;
  await chrome.storage.session.set({ [ATTEMPTS_KEY]: attempts });
  try {
    const saved = await connectPublisher({ tabId, host, root: publisherRoot(host, status.roots), user: status.user, origin: status.origin });
    publisherCache = { ...status, sessions: status.sessions.map((s) => (s.host === host ? { ...s, ...saved } : s)) };
    await chrome.storage.session.set({ [AUTO_KEY]: { host, at: now, ok: true } });
  } catch (err) {
    console.warn(`[gamma] automatic publisher-session refresh for ${host} failed: ${err.message}`);
    await chrome.storage.session.set({ [AUTO_KEY]: { host, at: now, ok: false, error: err.message } });
  }
}

// ---------- fetches a chat handed to this browser ----------

// A chat card whose fetch a publisher stopped (a CAPTCHA, a sign-in, a
// paywall) asks the worker to open that page (bridge.js "connector-tab":
// openHandoff) — next to the Gamma tab, switched to, or left in the
// background when the user chose "in the background" — or its "Open" goes
// through <server>/api/ai/handoffs/<id>/go on its way there (no Connector
// answered yet, or the desktop app's system browser). Either tab — and any
// tab it opens, like a "PDF" link with target=_blank — is bound to that
// request (POST …/watch, so the card says the Connector is on it). Each page
// such a tab finishes loading is a chance to download the PDF with the
// browser's session (bytesFromTab, the save pipeline's two attempts). When
// those fail on the page's PDF links, the tab itself opens the first, as a
// click would (handoff.js nextToOpen), and the PDF it ends on is taken on its
// next load. The first real PDF goes to the request (POST …/pdf); a tab the
// user saw brings the Gamma tab that asked forward, one in the background closes;
// a connected publisher's cookies are refreshed from the session that just
// worked. What the Connector is doing shows on the card (noteHandoff).
const HANDOFFS_KEY = "handoffs"; // tabId → {id, source, url, pdf_url, host, opener, app, opened, from, away, background, note}
const QUEUE_KEY = "handoff:queue";   // background opens waiting for a turn (handoff.js backgroundBusy)
const harvesting = new Set();     // request ids with a download in flight
const again = new Set();          // tabs that loaded while their request's download was in flight

async function handoffTabs() {
  return (await chrome.storage.session.get(HANDOFFS_KEY))[HANDOFFS_KEY] || {};
}

// Every change to the bindings and the queue goes through here, one at a
// time: several requests open, note and deliver at once, and a
// read-modify-write of either would drop another's change. `fn` changes the
// bindings (tabId → binding) and the queue in place, and must not call back
// in here; what it returns is returned.
let handoffLock = Promise.resolve();
function changeHandoffs(fn) {
  const run = handoffLock.then(async () => {
    const got = await chrome.storage.session.get([HANDOFFS_KEY, QUEUE_KEY]);
    const all = got[HANDOFFS_KEY] || {};
    const queue = got[QUEUE_KEY] || [];
    const out = await fn(all, queue);
    await chrome.storage.session.set({ [HANDOFFS_KEY]: all, [QUEUE_KEY]: queue });
    return out;
  });
  handoffLock = run.catch(() => {});
  return run;
}

// A tab's binding, set whole.
function bindTab(tabId, binding) {
  return changeHandoffs((all) => { all[tabId] = binding; });
}

// Fields of a tab's binding changed (never bringing a released one back);
// null lets the tab go.
function patchHandoffTab(tabId, patch) {
  return changeHandoffs((all) => {
    if (patch === null) delete all[tabId];
    else if (all[tabId]) all[tabId] = { ...all[tabId], ...patch };
  });
}

// The request is settled (or the card let it go): its tabs are no longer
// bound, and the background ones the Connector opened close.
async function releaseHandoff(id) {
  const closing = await changeHandoffs((all, queue) => {
    const out = [];
    for (const [tabId, b] of Object.entries(all)) {
      if (b.id !== id) continue;
      delete all[tabId];
      if (b.background) out.push(Number(tabId));
    }
    const i = queue.findIndex((b) => b.id === id);
    if (i >= 0) queue.splice(i, 1);
    return out;
  });
  for (const tabId of closing) { try { await chrome.tabs.remove(tabId); } catch {} }
  pumpQueue();
}

// A blank background tab for `binding`: next to the Gamma tab that asked,
// not switched to (the last focused window's, when that tab is gone).
async function backgroundTab(binding) {
  let gamma = null;
  if (binding.opener != null) { try { gamma = await chrome.tabs.get(binding.opener); } catch {} }
  return chrome.tabs.create({ url: "about:blank", active: false,
    ...(gamma ? { windowId: gamma.windowId, index: gamma.index + 1, openerTabId: gamma.id } : {}) });
}

// A freshly bound tab: tell the card the Connector took the request, then
// load the page (the binding is stored before it can finish loading).
async function loadBound(tabId, binding) {
  await noteHandoff(binding, "");
  try { await chrome.tabs.update(tabId, { url: binding.url }); } catch {}
}

// The next queued out-of-sight fetches, while turns are free.
let pumping = Promise.resolve();
function pumpQueue() {
  pumping = pumping.then(async () => {
    for (;;) {
      const started = await changeHandoffs(async (all, queue) => {
        if (!queue.length || backgroundBusy(all)) return null;
        const next = queue.shift();
        const tab = await backgroundTab(next);
        all[tab.id] = { ...next, background: true, note: "" };
        return { tabId: tab.id, binding: all[tab.id] };
      });
      if (!started) return;
      let req = null;
      try { req = await api(`/ai/handoffs/${encodeURIComponent(started.binding.id)}`); } catch {}
      if (req && req.status === "waiting") await loadBound(started.tabId, started.binding);
      else await releaseHandoff(started.binding.id); // settled while it waited
    }
  }).catch((err) => console.warn(`[gamma] queued fetches: ${err.message}`));
  return pumping;
}

// The card asked to open its request's page ("connector-tab" open): next to
// the Gamma tab that asked, switched to or left in the background
// (`background`). A page already
// open for the request is shown instead (or left where it is). "opened",
// or "queued" when every out-of-sight turn is taken.
async function openHandoff(req, sender, background) {
  const gamma = sender.tab || {};
  const binding = { id: req.id, source: req.source, url: req.url, pdf_url: req.pdf_url, host: req.host,
                    opener: gamma.id ?? null, app: sender.origin || "", background, note: "" };
  const got = await changeHandoffs(async (all, queue) => {
    if (Object.values(all).some((b) => b.id === req.id)) return { status: "open" };
    const waiting = queue.findIndex((b) => b.id === req.id);
    if (background) {
      if (waiting >= 0) return { status: "queued" };
      if (backgroundBusy(all)) { queue.push(binding); return { status: "queued" }; }
      const tab = await backgroundTab(binding);
      all[tab.id] = binding;
      return { status: "opened", tabId: tab.id };
    }
    if (waiting >= 0) queue.splice(waiting, 1);
    const tab = await chrome.tabs.create({ url: "about:blank", active: true, windowId: gamma.windowId,
                                          index: gamma.index != null ? gamma.index + 1 : undefined,
                                          openerTabId: gamma.id });
    all[tab.id] = binding;
    return { status: "opened", tabId: tab.id };
  });
  if (got.status === "open") {
    if (!background) await showHandoff(req.id, sender);
    return "opened";
  }
  if (got.tabId != null) await loadBound(got.tabId, binding);
  return got.status;
}

// The card asked to see its request's tab ("connector-tab" show): it comes
// forward (next to the Gamma tab that asked, if that moved to another
// window), and is the user's from then on: it stays open after delivery.
async function showHandoff(id, sender) {
  const entry = Object.entries(await handoffTabs()).find(([, b]) => b.id === id);
  if (!entry) return false;
  const tabId = Number(entry[0]);
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch { await patchHandoffTab(tabId, null); return false; }
  if (entry[1].background) {
    const gamma = sender && sender.tab;
    // Moved only when the Gamma tab is in another window now.
    if (gamma && gamma.windowId !== tab.windowId) {
      try { await chrome.tabs.move(tabId, { windowId: gamma.windowId, index: gamma.index + 1 }); } catch {}
    }
    await patchHandoffTab(tabId, { background: false });
    const seen = (await handoffTabs())[tabId];
    if (seen) await noteHandoff(seen, seen.note || "");
    pumpQueue();
  }
  try {
    tab = await chrome.tabs.get(tabId);
    await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  } catch {}
  return true;
}

async function bindHandoff(tabId, url, openerTabId) {
  const id = handoffIdFrom(url);
  if (!id || (await handoffTabs())[tabId]?.id === id) return;
  let req;
  // Another account's request, an expired one, or signed out: not ours to help.
  try { req = await api(`/ai/handoffs/${encodeURIComponent(id)}/watch`, { json: { note: "" } }); }
  catch (err) { console.warn(`[gamma] fetch request ${id} not taken: ${err.message}`); return; }
  if (req.status !== "waiting") return;
  // `app`: the address the Gamma page that asked is open at (the /go
  // address's), which need not be the one the Connector is set to.
  await bindTab(tabId, { id, source: req.source, url: req.url, pdf_url: req.pdf_url,
                         host: req.host, opener: openerTabId ?? null, app: new URL(url).origin,
                         background: false, note: "" });
}

// The Gamma tab to bring back: the one that opened the request, else the
// most recently used Gamma page at the address it asked from (or the
// Connector's own), never an API address like /go.
async function focusGamma({ opener, app }) {
  const origins = [app, await serverOrigin()].filter(Boolean);
  const isApp = (t) => t && t.url && origins.some((o) => t.url.startsWith(o + "/") && !t.url.startsWith(o + "/api/"));
  let tab = null;
  if (opener != null) { try { tab = await chrome.tabs.get(opener); } catch {} }
  if (!isApp(tab)) {
    tab = (await chrome.tabs.query({})).filter(isApp)
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0] || null;
  }
  if (!tab) return;
  try {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  } catch {}
}

// Tell the card what the Connector is doing — "looking" (no PDF link on the
// page yet), "check" (the page is a bot check or CAPTCHA), "signin" (the PDF
// link leads to a sign-in), "opening" (the tab opens one), "refused" (no link
// gave a PDF), "other" (the tab shows another paper), "closed" (the user
// closed the tab) — and whether the tab is in the background. The request's
// bindings remember it: a tab that waits for the user (NEEDS_YOU) frees its
// turn.
async function noteHandoff(bound, note) {
  const changed = await changeHandoffs((all) => {
    let any = false;
    for (const b of Object.values(all)) if (b.id === bound.id && b.note !== note) { b.note = note; any = true; }
    return any;
  });
  try {
    await api(`/ai/handoffs/${encodeURIComponent(bound.id)}/watch`, { json: { note, background: !!bound.background } });
  } catch {}
  if (changed && NEEDS_YOU.has(note)) pumpQueue();
}

async function harvestHandoff(tabId, attempt = 0) {
  const bound = (await handoffTabs())[tabId];
  if (!bound) return;
  if (harvesting.has(bound.id)) { again.add(tabId); return; }
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch { return; }
  const origin = await serverOrigin();
  if (!tab.url || !/^https?:/i.test(tab.url) || tab.url.startsWith(origin + "/") || handoffIdFrom(tab.url)) return;
  // Since a link was opened, has the tab been on another site (a sign-in)?
  if (bound.from && !bound.away && siteOf(tab.url) !== siteOf(bound.from)) {
    bound.away = true;
    await patchHandoffTab(tabId, { away: true });
  }
  harvesting.add(bound.id);
  try {
    let req;
    try { req = await api(`/ai/handoffs/${encodeURIComponent(bound.id)}`); }
    catch (err) { if (err.status === 404 || err.status === 401) await releaseHandoff(bound.id); return; }
    if (req.status !== "waiting") { await releaseHandoff(bound.id); return; }
    let fromPage = null;
    try { fromPage = await chrome.tabs.sendMessage(tabId, { type: "get-detection", fresh: true }); } catch {}
    const viewer = !fromPage; // Chrome's PDF viewer runs no content script
    const candidate = mergeCandidates(fromPage, candidateFromUrl(tab.url, tab.title));
    // Another paper's page (the user moved on, or the site shows another
    // version): its PDF must not answer this request — the card says why.
    if (!sameWork(candidate, bound)) { await noteHandoff(bound, "other"); return; }
    const urls = harvestUrls(candidate, bound, { tabUrl: tab.url, viewer });
    if (!urls.length) {
      // Publisher pages render their PDF link after they loaded, and a check
      // may pass by itself (a real browser often passes one unasked, more
      // slowly in a background tab): look again.
      const check = checkPage({ url: tab.url, title: tab.title, check: candidate.check });
      if (attempt < (check ? 4 : 2)) setTimeout(() => harvestHandoff(tabId, attempt + 1).catch(() => {}), 3000 * (attempt + 1));
      else await noteHandoff(bound, check ? "check" : "looking");
      return;
    }
    const tried = [];
    for (const url of urls) {
      let blob;
      try { blob = await bytesFromTab(url, tabId); }
      catch (err) { tried.push({ url, landed: err.landed || "" }); continue; } // a sign-in page, not yet
      const form = new FormData();
      form.append("file", blob, "paper.pdf");
      form.append("url", url);
      let out;
      try { out = await api(`/ai/handoffs/${encodeURIComponent(bound.id)}/pdf`, { form }); }
      catch (err) {
        console.warn(`[gamma] sending the PDF to the chat failed: ${err.message}`);
        if ([404, 409].includes(err.status)) await releaseHandoff(bound.id);
        return;
      }
      // The session that just worked refreshes a connected publisher's
      // cookies — before a background tab closes with the release.
      const refresh = !tab.incognito && /^https:/i.test(url)
        ? autoRefreshPublisher(tabId, tab.url, { force: true }).catch(() => {}) : null;
      if (((await handoffTabs())[tabId] || bound).background) {
        await refresh;
        await releaseHandoff(bound.id);
        return;
      }
      await releaseHandoff(bound.id);
      await notify(`Sent to your Gamma chat: ${out.pages} page${out.pages === 1 ? "" : "s"} from ${new URL(url).hostname}.`);
      await focusGamma(bound);
      return;
    }
    // No download gave a PDF: open the link in the tab, as a click would —
    // unless the downloads ended on a sign-in page (the user signs in first).
    const opened = bound.opened || {};
    const next = nextToOpen(tried, { tabUrl: tab.url, viewer, opened, away: bound.away });
    if (!next) { await noteHandoff(bound, needsSignIn(tried) ? "signin" : "refused"); return; }
    await patchHandoffTab(tabId, { opened: { ...opened, [next]: (opened[next] || 0) + 1 }, from: tab.url, away: false });
    await noteHandoff(bound, "opening");
    await chrome.tabs.update(tabId, { url: next });
  } finally {
    harvesting.delete(bound.id);
    if (again.delete(tabId)) harvestHandoff(tabId).catch(() => {});
  }
}

// ---------- the save pipeline ----------

async function progress(tabId, text) {
  await setTabState(tabId, { saving: text || "" });
}

async function looksLikePdf(blob) {
  const head = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
  return String.fromCharCode(...head).indexOf("%PDF") === 0;
}

const NOT_PDF_MSG = "this tab isn't a PDF (or the site sent a login page instead)";

// Atypon platforms (science.org & co.) serve the HTML reader at /doi/pdf/…
// unless ?download=true asks for the file itself — worth a second attempt.
function pdfUrlVariants(url) {
  const list = [url];
  try {
    const u = new URL(url);
    if (/\/doi\/e?pdf\//i.test(u.pathname) && !u.searchParams.has("download")) {
      u.pathname = u.pathname.replace(/\/doi\/epdf\//i, "/doi/pdf/");
      u.searchParams.set("download", "true");
      list.push(u.href);
    }
  } catch {}
  return list;
}

// A failed attempt remembers where its download ended (`landed` on the
// error): a sign-in page means the user has no access yet (harvestHandoff).
async function bytesFromTab(url, tabId) {
  // The browser's own session (institutional login, cookies) fetches what
  // the server can't. Two attempts per URL variant: the worker's direct
  // fetch, then — publisher bot checks 403 requests with an extension origin
  // and no Referer — the tab's content script, whose same-origin fetch looks
  // like the reader loading the PDF. Raw PDF tabs have no content script;
  // sendMessage fails there and the direct error stands.
  let lastErr = new Error("the browser couldn't download the PDF");
  let landed = "";
  for (const u of pdfUrlVariants(url)) {
    try {
      const res = await fetch(u, { credentials: "include" });
      landed = res.url || landed;
      if (!res.ok) throw new Error(`the browser couldn't download the PDF (${res.status})`);
      const blob = await res.blob();
      if (await looksLikePdf(blob)) return blob;
      throw new Error(NOT_PDF_MSG);
    } catch (err) {
      lastErr = err;
      console.warn(`[gamma] worker fetch failed for ${u}: ${err.message}`);
    }
    if (tabId == null) continue;
    let r = null;
    try { r = await chrome.tabs.sendMessage(tabId, { type: "fetch-pdf", url: u }); }
    catch (err) { console.warn(`[gamma] no content-script relay in tab ${tabId} (${err.message}) — is the tab reloaded?`); }
    if (r && r.ok && r.base64) {
      const bytes = Uint8Array.from(atob(r.base64), (c) => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: "application/pdf" });
      if (await looksLikePdf(blob)) return blob;
      lastErr = new Error(NOT_PDF_MSG);
      console.warn(`[gamma] in-page fetch of ${u} returned non-PDF bytes`);
    } else if (r && r.notPdf) {
      lastErr = new Error(NOT_PDF_MSG);
    } else if (r && (r.status || r.error)) {
      lastErr = new Error(r.error || `the browser couldn't download the PDF (${r.status})`);
      console.warn(`[gamma] in-page fetch failed for ${u}: ${lastErr.message}`);
    }
    if (r && r.landed) landed = r.landed;
  }
  lastErr.landed = landed;
  throw lastErr;
}

async function uploadBlob(tabId, blob, url, expectedOrigin) {
  const form = new FormData();
  const name = (decodeURIComponent(url.split("?")[0].split("/").pop() || "") || "paper.pdf").replace(/\.pdf$/i, "") + ".pdf";
  form.append("file", blob, name);
  if (tabId != null) await progress(tabId, "uploading…");
  const up = await api("/uploads", { form, expectedOrigin });
  return up.doc_id;
}

async function savePaper({ tabId, candidate, folder, labels, title, source_url }) {
  const settings = await getSettings();
  const cand = candidate || { kind: "none", source_url: source_url || "" };
  // A PDF tab has no title of its own; the registry record previewed for the
  // popup names the page right away (auto_title — the metadata lookup may
  // still replace it, a user rename never is).
  const st = tabId != null ? await getTabState(tabId) : {};
  const previewTitle = st.preview && st.preview.title || "";
  const payload = {
    source_url: cand.source_url || source_url || "",
    pdf_url: cand.pdf_url || "", doi: cand.doi || "", arxiv_id: cand.arxiv_id || "",
    title: title != null ? title : (cand.title || previewTitle),
    folder: folder != null ? folder : settings.folder,
    labels: labels != null ? labels : settings.labels,
    allow_oa: settings.allowOa, save_copy: settings.saveCopy,
  };
  // The URL this browser could download itself: the tab that *is* a PDF, or
  // the page's advertised PDF link.
  const fetchUrl = cand.pdf_url || (cand.is_pdf_tab ? cand.source_url : "");
  if (tabId != null) await progress(tabId, "resolving…");
  try {
    if (cand.is_pdf_tab && fetchUrl) {
      // The tab is the PDF — upload the bytes the browser already has access
      // to instead of making the server re-download (it may not be able to).
      // Best-effort: on failure the server-side resolve below still runs.
      try {
        if (tabId != null) await progress(tabId, "downloading in your browser…");
        payload.doc_id = await uploadBlob(tabId, await bytesFromTab(fetchUrl, tabId), fetchUrl, settings.server);
      } catch (err) { console.warn(`[gamma] browser-first upload failed, server will try: ${err.message}`); }
    }
    if (tabId != null) await progress(tabId, "saving to your library…");
    let out;
    try {
      out = await api("/clip", { json: payload, expectedOrigin: settings.server });
    } catch (err) {
      // The server couldn't fetch the PDF (paywall, bot check) — this
      // browser's session often can. Download here, upload, save again.
      if (!(err instanceof ApiError) || err.status !== 400 || payload.doc_id || !fetchUrl) throw err;
      if (tabId != null) await progress(tabId, "server couldn't fetch it — downloading in your browser…");
      let blob;
      try { blob = await bytesFromTab(fetchUrl, tabId); }
      catch (bErr) { throw new Error(`${err.message} The browser-side download failed too: ${bErr.message}.`); }
      payload.doc_id = await uploadBlob(tabId, blob, fetchUrl, settings.server);
      if (tabId != null) await progress(tabId, "saving to your library…");
      out = await api("/clip", { json: payload, expectedOrigin: settings.server });
    }
    if (tabId != null && await serverOrigin() === settings.server) await setTabState(tabId, { saving: "", hit: out, last: out, error: "", origin: settings.server });
    return out;
  } catch (err) {
    const message = err.message || "save failed";
    if (tabId != null) await setTabState(tabId, { saving: "", error: message, auth: err.status === 401 ? false : undefined });
    if (err.status === 401) authCache.at = 0;
    throw err;
  }
}

async function clipSelection({ tabId, text, source_url, title }) {
  const origin = await serverOrigin();
  const st = tabId != null ? await getTabState(tabId) : {};
  const page_id = st.origin === origin && st.hit && st.hit.block_id || "";
  return api("/clip/note", { json: { text, source_url, title, page_id }, expectedOrigin: origin });
}

// ---------- notifications (context menu + shortcut results) ----------

const notifyTargets = new Map();

async function notify(message, openUrl) {
  if (!chrome.notifications) return;
  const id = `gamma-${Date.now()}`;
  if (openUrl) notifyTargets.set(id, openUrl);
  try {
    await chrome.notifications.create(id, { type: "basic", iconUrl: "assets/icons/icon128.png", title: "Gamma", message: String(message).slice(0, 300) });
  } catch {}
}

chrome.notifications && chrome.notifications.onClicked.addListener((id) => {
  const url = notifyTargets.get(id);
  if (url) chrome.tabs.create({ url });
  notifyTargets.delete(id);
  chrome.notifications.clear(id);
});

async function openInGamma(out) {
  const origin = await serverOrigin();
  return origin + (out.open_url || "/");
}

// ---------- tabs ----------

async function ensureDetection(tabId) {
  const st = await getTabState(tabId);
  if (st.candidate && st.looked && st.origin === await serverOrigin()) return st;
  let tab = null;
  try { tab = await chrome.tabs.get(tabId); } catch { return st; }
  let fromPage = null;
  try { fromPage = await chrome.tabs.sendMessage(tabId, { type: "get-detection" }); } catch {}
  const candidate = mergeCandidates(fromPage, candidateFromUrl(tab.url, tab.title));
  return setDetection(tabId, candidate);
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.url) bindHandoff(tabId, info.url, tab && tab.openerTabId).catch(() => {});
  if (info.status === "loading" && info.url) {
    chrome.storage.session.remove(key(tabId));
    updateBadge(tabId, {}).catch(() => {});
  }
  if (info.status === "complete" && tab && tab.url) {
    // The content script reports first on normal pages; this fills in for PDF
    // tabs and pages where it can't run.
    setTimeout(async () => {
      const st = await getTabState(tabId);
      if (!st.candidate) setDetection(tabId, candidateFromUrl(tab.url, tab.title)).catch(() => {});
    }, 800);
    if (!tab.incognito && /^https:/i.test(tab.url)) autoRefreshPublisher(tabId, tab.url).catch(() => {});
    harvestHandoff(tabId).catch((err) => console.warn(`[gamma] fetch for the chat: ${err.message}`));
  }
});

// A tab a bound tab opens (a "PDF" link with target=_blank) works for the
// same request; the /go tab itself may be known only by its pending URL.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (tab.pendingUrl || tab.url) bindHandoff(tab.id, tab.pendingUrl || tab.url, tab.openerTabId).catch(() => {});
  if (tab.openerTabId == null) return;
  const parent = (await handoffTabs())[tab.openerTabId];
  if (parent) await bindTab(tab.id, { ...parent });
});

// A request's last tab closed before its PDF came: the card says so.
chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(key(tabId));
  (async () => {
    const bound = (await handoffTabs())[tabId];
    await patchHandoffTab(tabId, null);
    if (bound && !Object.values(await handoffTabs()).some((b) => b.id === bound.id)) {
      await noteHandoff({ ...bound, background: false }, "closed");
    }
    await pumpQueue();
  })().catch(() => {});
});

// ---------- messages ----------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg && msg.type) {
      case "detected": {
        const tabId = sender.tab && sender.tab.id;
        if (tabId == null) return null;
        const st = await getTabState(tabId);
        const merged = mergeCandidates(msg.candidate, st.candidate && st.candidate.from_url ? st.candidate : candidateFromUrl(sender.tab.url, sender.tab.title));
        return setDetection(tabId, merged);
      }
      case "get-state": {
        const auth = await checkAuth(!!msg.forceAuth);
        let st = {};
        if (msg.tabId != null) {
          st = auth.configured && auth.auth ? await ensureDetection(msg.tabId) : await getTabState(msg.tabId);
          if (!st.candidate) {
            try { const tab = await chrome.tabs.get(msg.tabId); st.candidate = candidateFromUrl(tab.url, tab.title); } catch {}
          }
        }
        return { ...st, ...auth, settings: await getSettings() };
      }
      case "save":
        return savePaper(msg);
      case "clip-selection": {
        let text = msg.text || "";
        if (!text && msg.tabId != null) {
          try { text = (await chrome.tabs.sendMessage(msg.tabId, { type: "get-selection" })).text; } catch {}
        }
        return clipSelection({ ...msg, text });
      }
      case "auth-changed":
        authCache.at = 0;
        publisherCache = null;
        return checkAuth(true);
      // The popup's cookie button: connection metadata + the last automatic
      // refresh. `force` after the popup connected or disconnected a host.
      case "publisher-status": {
        const status = await publisherStatus(!!msg.force);
        const auto = (await chrome.storage.session.get(AUTO_KEY))[AUTO_KEY] || null;
        return { ...status, auto };
      }
      case "open": {
        const origin = await serverOrigin();
        await chrome.tabs.create({ url: origin + (msg.path || "/") });
        return true;
      }
      // A chat card asking (bridge.js) whether this Connector can fetch for
      // its request. The answer comes from the Connector's own server; a
      // page that is not that server's app hears only "ok", and nothing
      // when its request is not one this Connector can serve.
      case "connector-probe": {
        const origin = await serverOrigin();
        const own = !!origin && sender.origin === origin;
        const id = String(msg.id || "");
        if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) return null;
        try {
          await api(`/ai/handoffs/${encodeURIComponent(id)}`);
          return { status: "ok" };
        } catch (err) {
          if (!own) return null;
          return { status: err.status === 401 ? "signed-out" : [403, 404].includes(err.status) ? "other-account" : "unreachable" };
        }
      }
      // A chat card asking (bridge.js) to open its request's page — next to
      // it, or in the background — to show that tab, or to let it go (Dismiss).
      // Only a request of this Connector's account, so a page learns nothing
      // and opens nothing without one of its ids.
      case "connector-tab": {
        const id = String(msg.id || "");
        if (!/^[A-Za-z0-9_-]{8,64}$/.test(id) || !sender.tab) return null;
        let req;
        try { req = await api(`/ai/handoffs/${encodeURIComponent(id)}`); } catch { return null; }
        if (msg.do === "open") {
          if (req.status !== "waiting") return { status: req.status };
          return { status: await openHandoff(req, sender, !!msg.background) };
        }
        if (msg.do === "show") return { status: (await showHandoff(id, sender)) ? "shown" : "none" };
        if (msg.do === "close") { await releaseHandoff(id); return { status: "closed" }; }
        return null;
      }
      default:
        return null;
    }
  })().then((r) => sendResponse({ ok: true, result: r }),
            (err) => sendResponse({ ok: false, error: err.message || String(err), status: err.status || 0 }));
  return true;
});

// ---------- context menus + keyboard command ----------

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "gamma-save-link", title: "Save link to Gamma", contexts: ["link"] });
    chrome.contextMenus.create({ id: "gamma-save-page", title: "Save page to Gamma", contexts: ["page"] });
    chrome.contextMenus.create({ id: "gamma-clip-selection", title: "Clip selection to Gamma", contexts: ["selection"] });
  });
});

async function savePageFromTab(tab) {
  const st = await ensureDetection(tab.id);
  if (st.hit) { await notify(`Already in your library: ${st.hit.title}`, await openInGamma(st.hit)); return; }
  const cand = st.candidate || candidateFromUrl(tab.url, tab.title);
  if (cand.kind === "none") { await notify("No paper or PDF found on this page."); return; }
  try {
    const out = await savePaper({ tabId: tab.id, candidate: cand });
    await notify(`${out.existed ? "Already in your library" : "Saved"}: ${out.title}`, await openInGamma(out));
  } catch (err) {
    await notify(`Save failed: ${err.message}`);
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    if (info.menuItemId === "gamma-save-link") {
      const out = await savePaper({ tabId: null, candidate: candidateFromUrl(info.linkUrl, ""), source_url: info.linkUrl, title: "" });
      await notify(`${out.existed ? "Already in your library" : "Saved"}: ${out.title}`, await openInGamma(out));
    } else if (info.menuItemId === "gamma-save-page" && tab) {
      await savePageFromTab(tab);
    } else if (info.menuItemId === "gamma-clip-selection" && tab) {
      const out = await clipSelection({ tabId: tab.id, text: info.selectionText || "", source_url: tab.url, title: tab.title });
      await notify("Clipped to Gamma.", await openInGamma(out));
    }
  } catch (err) {
    await notify(`Gamma: ${err.message}`);
  }
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== "save-to-gamma") return;
  if (!tab) [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) await savePageFromTab(tab);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.server) {
    authCache.at = 0;
    publisherCache = null;
    // Library hits and badges belong to the server that resolved them.
    chrome.storage.session.get(null).then(async (stored) => {
      for (const [k, st] of Object.entries(stored)) {
        if (k.startsWith("tab:") && st.origin !== changes.server.newValue) {
          await setTabState(Number(k.slice(4)), { hit: null, last: null, looked: false, auth: null, saving: "", error: "" });
        }
      }
    }).catch(() => {});
  }
});

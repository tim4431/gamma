// The ink files behind the page's handwriting blocks, for the document's
// life: loaded files by URL, and per-block DRAFTS — the strokes as edited
// here, ahead of (or between) uploads. A draft wins over the block's file
// until the upload replaces the block's `ink_url` with the draft's; a
// remote change of `ink_url` on a block with no unsaved strokes drops the
// draft. Plain module state with a version counter — React subscribes
// through useInkVersion (ink/InkLayer.jsx).
import { API, apiJson, makeId, getCurrentWorkspace, getExpectedUser, getShareToken } from "../shared/lib/utils";

const files = new Map();     // scoped URL → ink | null (null: fetch failed)
const loading = new Map();   // scoped URL → Promise
const drafts = new Map();    // scoped block ID → drawing and save/recovery state
const listeners = new Set();
let version = 0;
let persistenceProblem = false;

// Completed strokes remain recoverable after a tab reload while offline or
// while a conflicting save awaits a decision. Asset caches stay disposable.
const database = new Promise((resolve) => {
  if (typeof indexedDB === "undefined") { resolve(null); return; }
  try {
    const request = indexedDB.open("gamma-ink-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts", { keyPath: "key" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { persistenceProblem = true; resolve(null); };
    request.onblocked = () => { persistenceProblem = true; resolve(null); };
  } catch { persistenceProblem = true; resolve(null); }
});
function persist(d) {
  database.then((db) => {
    if (!db) return;
    const tx = db.transaction("drafts", "readwrite");
    tx.onerror = () => { persistenceProblem = true; bump(); };
    const table = tx.objectStore("drafts");
    if (d.dirty) table.put({ ...d, conflict: !!d.conflict });
    else table.delete(d.key);
  }).catch(() => { persistenceProblem = true; bump(); });
}
export const ready = database.then((db) => new Promise((resolve) => {
  if (!db) { resolve(); return; }
  const req = db.transaction("drafts").objectStore("drafts").getAll();
  req.onsuccess = () => {
    for (const d of req.result) if (!drafts.has(d.key)) drafts.set(d.key, d);
    bump(); resolve();
  };
  req.onerror = () => resolve();
})).catch(() => { persistenceProblem = true; bump(); });

const scope = () => JSON.stringify([getExpectedUser(), getCurrentWorkspace(), getShareToken()]);
export const currentScope = scope;
const scoped = (id) => `${scope()}|${id}`;

function bump() {
  version += 1;
  for (const fn of listeners) fn(version);
}
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function currentVersion() { return version; }

export function loadInk(url, { retry = false } = {}) {
  if (!url) return Promise.resolve(null);
  const key = scoped(url);
  if (retry && files.get(key) === null) files.delete(key);
  if (files.has(key)) return Promise.resolve(files.get(key));
  if (loading.has(key)) return loading.get(key);
  const p = apiJson(`${API}${url.replace(/^\/api/, "")}`)
    .then((ink) => { files.set(key, ink && ink.format === "gamma-ink" ? ink : null); return files.get(key); })
    .catch(() => { files.set(key, null); return null; })
    .finally(() => { loading.delete(key); bump(); });
  loading.set(key, p);
  return p;
}

// The strokes to show for a block: its draft, else its file (fetch kicked
// off when unseen — the caller re-renders on the store's next bump).
export function inkFor(block) {
  const url = block?.properties?.ink_url || "";
  const key = scoped(block?.id), d = drafts.get(key);
  if (d) {
    if (!d.dirty && d.url !== url && !d.awaitingUrls?.includes(url)) drafts.delete(key);
    else {
      if (d.url === url) d.awaitingUrls = [];
      return d.ink;
    }
  }
  if (!url) return null;
  if (!files.has(scoped(url))) { loadInk(url); return null; }
  return files.get(scoped(url));
}

if (typeof window !== "undefined") {
  const retryFailed = () => {
    let changed = false;
    for (const [key, ink] of files) if (ink === null) { files.delete(key); changed = true; }
    if (changed) bump();
  };
  window.addEventListener("online", retryFailed);
  window.addEventListener("focus", retryFailed);
}

export function draft(id) { return drafts.get(scoped(id)) || null; }
export function setDraft(id, ink, { baseUrl = null, pageId = "", paper } = {}) {
  const key = scoped(id), prev = drafts.get(key);
  drafts.set(key, { ...prev, id, key, scope: scope(), ink, dirty: true,
    batch: makeId(), recovery: null,
    paper: paper || prev?.paper,
    pageId: pageId || prev?.pageId || "", url: prev?.url ?? baseUrl,
    baseUrl: prev?.dirty ? prev.baseUrl : prev?.awaitingUrls?.length ? prev.url : baseUrl, conflict: prev?.conflict || false });
  persist(drafts.get(key));
  bump();
}
// The upload of `ink` landed at `url`. Strokes added meanwhile keep the
// draft dirty (the next flush uploads them).
export function markSaved(id, ink, url, key = scoped(id)) {
  const d = drafts.get(key);
  if (!d) return;
  files.set(`${d.scope}|${url}`, ink);
  // An HTTP acknowledgement can precede its websocket echo. Keep showing
  // the acknowledged drawing while the tree still names a predecessor.
  const awaitingUrls = [...new Set([...(d.awaitingUrls || []), d.baseUrl])].filter((previous) => previous !== url);
  drafts.set(key, { ...d, url, baseUrl: url, awaitingUrls, dirty: d.ink !== ink, conflict: false });
  persist(drafts.get(key));
  bump();
}
export function recoveryUnavailable() { return persistenceProblem; }
export function dirtyDrafts() {
  return [...drafts.values()].filter((d) => d.scope === scope() && d.dirty && !d.conflict);
}
export function conflicts() { return [...drafts.values()].filter((d) => d.scope === scope() && d.conflict); }
export function markConflict(key, error) {
  const d = drafts.get(key);
  if (d) { drafts.set(key, { ...d, conflict: error }); persist(drafts.get(key)); bump(); }
}
export function recoveryFor(draft) {
  const current = drafts.get(draft.key);
  if (current?.batch === draft.batch && current.recovery) return current.recovery;
  const recovery = { id: makeId(), batch: makeId() };
  if (current?.batch === draft.batch) { current.recovery = recovery; persist(current); }
  return recovery;
}
export function discardDraft(key) { drafts.delete(key); persist({ key, dirty: false }); bump(); }
export function isCurrentScope(value) { return value === scope(); }

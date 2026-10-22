// The ink files behind the page's handwriting blocks, for the document's
// life: loaded files by URL, and per-block DRAFTS — the strokes as edited
// here, ahead of (or between) uploads. A draft wins over the block's file
// until the upload replaces the block's `ink_url` with the draft's; a
// remote change of `ink_url` on a block with no unsaved strokes drops the
// draft. A draft remembers its BASE, the file its strokes were edited
// from, and that file's stroke ids: the save sends both along, so the
// server merges these strokes into a drawing someone else changed
// meanwhile instead of replacing it, by id even when the base file is gone
// (docs/dev/handwriting.md "Two writers, one group"). A version 1 file a
// not-yet-upgraded copy serves is converted as it loads and never written
// back as version 1. A file loaded again for a block (someone saved, a
// merge's result) keeps the strokes already on screen as they are
// (adoptStrokes), so their outlines are not built again. Every dirty draft
// is copied to the draft journal (inkJournal.js) until its upload lands,
// and a page open restores what a closed tab left unsaved — never a stroke
// the server deleted since. Plain module state with a version counter —
// React subscribes through useInkVersion (ink/InkLayer.jsx).
import { API, apiJson, getCurrentWorkspace, sha256 } from "../shared/lib/utils";
import { loadedInk, mergeInk, sameStroke, serializeInk } from "./ink.js";
import * as journal from "./inkJournal.js";

const files = new Map();     // url → ink | null (null: fetch failed)
const loading = new Map();   // url → Promise
const drafts = new Map();    // block id → {ink, dirty, url, base, baseIds, sent}
const shown = new Map();     // block id → the drawing inkFor last gave for it (a reload adopts from it)
const blockUrls = new Map(); // block id → its ink_url as inkFor was last asked (a restore reads it after its awaits)
const listeners = new Set();
let version = 0;

function bump() {
  version += 1;
  for (const fn of listeners) fn(version);
}
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function currentVersion() { return version; }

const strokeIds = (ink) => (ink?.strokes || []).map((s) => s.id);
function samePts(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// A file fetched again (a merge's result, a reload after someone saved)
// with what `known` — the drawing on screen — already has, matched by
// stroke id: a stroke equal to one there is that very object, and one with
// the same samples keeps that `pts` array (a recolour, a line restyle), so
// the caches ink.js keys by the array (decoded samples, bounds, outlines)
// are reused and nothing is built again. The same object when nothing is.
export function adoptStrokes(ink, known) {
  if (!ink?.strokes?.length || !known?.strokes?.length) return ink;
  const byId = new Map(known.strokes.map((s) => [s.id, s]));
  let reused = false;
  const strokes = ink.strokes.map((s) => {
    const k = byId.get(s.id);
    if (!k || k === s || !samePts(k.pts, s.pts)) return s;
    reused = true;
    // the samples compared once, as integers; then the rest of the keys
    return sameStroke({ ...k, pts: null }, { ...s, pts: null }) ? k : { ...s, pts: k.pts };
  });
  return reused ? { ...ink, strokes } : ink;
}

// A stored file fetched by its `/api/uploads/…` URL (ink.js loadedInk
// makes it a drawing).
const fetchUpload = (url) => apiJson(`${API}${url.replace(/^\/api/, "")}`);

// `known()` (optional): the drawing the file replaces on screen, asked
// when the file arrives, for adoptStrokes.
export function loadInk(url, known = null) {
  if (!url) return Promise.resolve(null);
  if (files.has(url)) return Promise.resolve(files.get(url));
  if (loading.has(url)) return loading.get(url);
  const p = fetchUpload(url)
    .then((ink) => {
      const file = loadedInk(ink);
      const before = file && typeof known === "function" ? known() : null;
      files.set(url, before ? adoptStrokes(file, before) : file);
      return files.get(url);
    })
    .catch(() => { files.set(url, null); return null; })
    .finally(() => { loading.delete(url); bump(); });
  loading.set(url, p);
  return p;
}

// The strokes to show for a block: its draft, else its file (fetch kicked
// off when unseen — the caller re-renders on the store's next bump).
export function inkFor(block) {
  const id = block?.id;
  const url = block?.properties?.ink_url || "";
  if (id) {
    blockUrls.set(id, url);
    restoreFor(block);
  }
  const d = drafts.get(id);
  if (d) {
    if (!d.dirty && d.url !== url) { drafts.delete(id); }
    else return seen(id, d.ink);
  }
  if (!url) return null;
  if (!files.has(url)) { loadInk(url, () => shown.get(id)); return null; }
  return seen(id, files.get(url));
}
function seen(id, ink) {
  if (id && ink) shown.set(id, ink);
  return ink;
}
// What a layer draws for a block: inkFor's drawing, or — while the file a
// block now names is on its way (someone saved) — the one it showed last,
// so a reload never blinks the group out. Edits read inkFor, never this:
// they must wait for the file they change.
export function inkShown(block) {
  const ink = inkFor(block);
  if (ink || !block?.id) return ink;
  const url = block.properties?.ink_url || "";
  return url && loading.has(url) ? shown.get(block.id) || null : null;
}

export function draft(id) { return drafts.get(id) || null; }
// `base`: the ink_url the strokes were edited from, for a block that has no
// draft yet (a draft keeps the base it started from, and the ids of the
// strokes that file had as it was loaded here). `sent`: the uploads whose
// block PUT went without an answer yet (markSending), oldest first.
export function setDraft(id, ink, base = "") {
  const prev = drafts.get(id);
  drafts.set(id, prev
    ? { ...prev, ink, dirty: true }
    : { ink, dirty: true, url: "", base, baseIds: base && files.get(base) ? strokeIds(files.get(base)) : [], sent: [] });
  nameLater(ink);
  journalLater(id);
  bump();
}
// The upload a draft would be — its bytes and the name the server gives
// them (`uploads/<sha24>.ink`, storage.content_digest) — worked out while
// the page is idle, so the flush on pagehide can send the block's PUT
// beside the upload instead of after it: nothing is sure to run once the
// page is gone. A draft edited again before its turn is skipped.
const DIGEST_CHARS = 24;
// How long every upload's name is, for sizing a block PUT before its
// upload is named (App's flush, its keepalive budget).
export const UPLOAD_NAME_CHARS = "/api/uploads/".length + DIGEST_CHARS + ".ink".length;
const named = new WeakMap();   // ink → {body, url}
const bodies = new WeakMap();  // ink → its serializeInk bytes (the upload's, the journal's)
const bytesOf = (ink) => {
  let body = bodies.get(ink);
  if (body === undefined) { body = serializeInk(ink); bodies.set(ink, body); }
  return body;
};
const idle = typeof requestIdleCallback === "function"
  ? (fn) => requestIdleCallback(fn, { timeout: 1000 }) : (fn) => setTimeout(fn, 50);
// The name the server gives an upload of these bytes.
const uploadName = async (body) => `/api/uploads/${(await sha256(body)).slice(0, DIGEST_CHARS)}.ink`;
function nameLater(ink) {
  idle(() => {
    if (named.has(ink) || ![...drafts.values()].some((d) => d.ink === ink)) return;
    const body = bytesOf(ink);
    uploadName(body).then((url) => named.set(ink, { body, url })).catch(() => {});
  });
}
// The upload of `ink` landed at `url` and the block now names `stored`:
// the same file, or — another writer changed the drawing meanwhile — the
// server's merge of both. Strokes added meanwhile keep the draft dirty,
// based on `url`, the file they were drawn onto; once the merged file is
// here it replaces the draft, with those strokes merged onto it. The PUTs
// sent before this one are answered by it (their strokes are in `url`).
export function markSaved(id, ink, url, stored = url) {
  const d = drafts.get(id);
  if (!d) return;
  files.set(url, ink);
  const sent = d.sent || [], at = sent.indexOf(url);
  drafts.set(id, { ...d, url: stored, base: url, baseIds: strokeIds(ink), dirty: d.ink !== ink, sent: at < 0 ? sent : sent.slice(at + 1) });
  if (d.ink === ink) journalDrop(id); else journalLater(id);
  bump();
  if (stored === url) return;
  loadInk(stored).then((loaded) => {
    const cur = drafts.get(id);
    if (!loaded || !cur || cur.url !== stored) return;
    const merged = adoptStrokes(loaded, cur.ink);
    files.set(stored, merged);
    const drawn = cur.ink !== ink;   // strokes added meanwhile, merged onto the merged file
    drafts.set(id, { ...cur, ink: drawn ? mergeInk(ink, cur.ink, merged).ink : merged, dirty: drawn, url: stored, base: stored,
      baseIds: strokeIds(merged) });
    if (drawn) journalLater(id);
    bump();
  });
}
// The block's PUT naming `url`, the upload of the draft as it is or was,
// is about to go. Until its answer comes (markSaved) or says it did not
// land (markUnsent), the journal entry names it: a page open after this
// tab closed then counts the entry's change from that file, since the PUT
// may have landed unanswered (a pagehide flush, a tab frozen in the
// background), so nothing the server deleted since comes back
// (inkJournal.js restoredInk). → the journal write, resolved once it is
// stored, at once without a journal, and after SENT_WAIT_MS at most: a
// journal that hangs (an IndexedDB open that never answers) never holds a
// save back.
const SENT_KEEP = 8;
const SENT_WAIT_MS = 500;
export function markSending(id, url) {
  const d = drafts.get(id);
  if (!d?.dirty || !url) return Promise.resolve(null);
  drafts.set(id, { ...d, sent: [...(d.sent || []).filter((u) => u !== url), url].slice(-SENT_KEEP) });
  journalCancel(id);
  const write = journalNow(id);
  if (!write) return Promise.resolve(null);
  let timer = 0;
  return Promise.race([write, new Promise((resolve) => { timer = setTimeout(resolve, SENT_WAIT_MS, null); })])
    .finally(() => clearTimeout(timer));
}
// The PUT naming `url` was answered with an error: it did not land.
export function markUnsent(id, url) {
  const d = drafts.get(id);
  if (!d?.sent?.includes(url)) return;
  drafts.set(id, { ...d, sent: d.sent.filter((u) => u !== url) });
  journalLater(id);
}
// An acknowledged deletion still masks the old file while its block is in
// the tree. Do not cache the empty ink under the old file URL, or discard
// an edit made while the delete request was pending.
export function markDeleted(id, ink, url) {
  const d = drafts.get(id);
  if (d?.ink === ink) {
    drafts.set(id, { ...d, dirty: false, url, base: url, baseIds: [] });
    journalDrop(id);
  }
}
// A block that comes back into the tree after its deletion (an undo of an
// erasure, a cut, a merge) is a fresh block: its next draft starts from no
// base. Left in place, the deleted draft's base (the old file) would make
// the server merge the re-upload three ways against the new block's empty
// drawing, and the strokes brought back would count as deleted there.
export function dropDraft(id) {
  drafts.delete(id);
  journalDrop(id);
}
// `upload`: the draft's bytes and name when they are worked out already
// (nameLater), else null.
export function dirtyDrafts() {
  return [...drafts.entries()].filter(([, d]) => d.dirty)
    .map(([id, d]) => ({ id, ink: d.ink, base: d.base, baseIds: d.baseIds, upload: named.get(d.ink) || null }));
}

// --- the draft journal (inkJournal.js) -----------------------------------------
// A draft's entry is written JOURNAL_MS after it turns dirty, at once when
// its PUT goes (markSending) and on pagehide, and deleted when its upload
// lands (markSaved, markDeleted) or the block starts over (dropDraft), so
// an entry that outlives its tab is what a flush never landed, or may have
// landed unanswered (its `sent`). Entries carry the tab that wrote them,
// which holds a Web Lock named after itself while it lives, and are keyed
// by it: a page open restores only entries of tabs that are gone, never
// this tab's own or a tab still open (it is about to save them itself).
const JOURNAL_MS = 300;
const TAB = Math.random().toString(36).slice(2, 10);
const LOCK = (tab) => `gamma-ink-tab-${tab}`;
export const journalTab = () => TAB;
const journalKeys = new Map();     // block id → the key its entry is written under
const journalTimers = new Map();   // block id → the pending write
let lockHeld = false;
function holdTabLock() {
  if (lockHeld) return;
  lockHeld = true;
  try { globalThis.navigator?.locks?.request?.(LOCK(TAB), () => new Promise(() => {}))?.catch?.(() => {}); } catch { /* no Web Locks */ }
}
async function tabAlive(tab) {
  if (!tab) return false;
  if (tab === TAB) return true;
  try {
    const state = await globalThis.navigator?.locks?.query?.();
    return !!state?.held?.some((lock) => lock.name === LOCK(tab));
  } catch { return false; }
}
function journalLater(id) {
  clearTimeout(journalTimers.get(id));
  journalTimers.set(id, setTimeout(() => { journalTimers.delete(id); journalNow(id); }, JOURNAL_MS));
}
// Calls off the draft's pending write: one goes at once instead, or none is due.
function journalCancel(id) {
  clearTimeout(journalTimers.get(id));
  journalTimers.delete(id);
}
const entryOf = (d) => ({ bytes: bytesOf(d.ink), base: d.base || "", baseIds: d.baseIds || [], at: Date.now(), tab: TAB, sent: d.sent || [] });
function keyOf(id) {
  const key = journalKeys.get(id) || journal.journalKey(getCurrentWorkspace(), id, TAB);
  journalKeys.set(id, key);
  return key;
}
function journalNow(id) {
  const d = drafts.get(id);
  if (!d?.dirty) return null;
  holdTabLock();
  return journal.putEntry(keyOf(id), entryOf(d));
}
function journalDrop(id) {
  journalCancel(id);
  const key = keyOf(id);
  journalKeys.delete(id);
  journal.deleteEntry(key);
}
// Every dirty draft's entry, now (the page is going away); → the writes.
export function journalAll() {
  const writes = [];
  for (const [id, d] of drafts) {
    if (!d.dirty) continue;
    journalCancel(id);
    writes.push(journalNow(id));
  }
  return Promise.all(writes);
}
if (typeof window !== "undefined" && typeof window.addEventListener === "function") window.addEventListener("pagehide", journalAll);

// Restoring: the workspace's entries are read once per tab (the first
// block asked for), and a block with any is restored the first time it is
// shown, one entry a visit — journal.restoredInk decides what comes back.
// The result is the block's dirty draft, based on the file the block
// names now, so it saves like any edit, and its entry becomes this tab's
// in the same write; another closed tab's entry for the block, and every
// entry of a block this tab drew on first, waits for the next visit. A
// block whose file changes while its restore runs is restored again,
// against the new file. onInkRestored's listeners hear of it ({id, ink,
// prior, ids}: the restored drawing, the block's file it was laid over,
// the strokes it adds), App with the pill and its Discard.
let entries = { ws: null, map: null };   // map: block id → [{key, entry}]
const restoring = new Set();
const restoredListeners = new Set();
const restoredQueue = [];
export function onInkRestored(fn) {
  restoredListeners.add(fn);
  while (restoredQueue.length) fn(restoredQueue.shift());
  return () => restoredListeners.delete(fn);
}
function restoreFor(block) {
  const ws = getCurrentWorkspace();
  if (entries.ws !== ws) {
    entries = { ws, map: null };
    const prefix = journal.journalPrefix(ws);
    journal.readEntries(prefix).then((found) => {
      if (entries.ws !== ws) return;
      const map = new Map();
      for (const [key, entry] of found) {
        const id = journal.keyBlock(key.slice(prefix.length));
        if (!map.has(id)) map.set(id, []);
        map.get(id).push({ key, entry });
      }
      entries.map = map;
      if (map.size) bump();
    });
    return;
  }
  const hits = entries.map?.get(block.id);
  if (!hits || restoring.has(block.id)) return;
  entries.map.delete(block.id);
  if (drafts.has(block.id)) return;   // edited here already: this tab's draft rules
  restoring.add(block.id);
  let again = false;
  (async () => {
    for (const hit of hits) {
      if (drafts.has(block.id)) return;   // one restored, or drawn on here meanwhile: the rest wait
      if ((await restore(block, hit)) === "again") {
        again = true;
        entries.map?.set(block.id, [...(entries.map.get(block.id) || []), hit]);
      }
    }
  })().catch(() => {}).finally(() => {
    restoring.delete(block.id);
    if (again) bump();   // asked again, the block is restored against its new file
  });
}
const blockUrl = (block) => blockUrls.get(block.id) ?? (block.properties?.ink_url || "");
// The block's file as the server has it: {ink}, {gone} for a 404, {error}.
async function fetchFile(url) {
  try {
    return { ink: loadedInk(await fetchUpload(url)) };
  } catch (err) {
    return err?.status === 404 ? { gone: true } : { error: true };
  }
}
// Whether the block's `url` is an upload the entry's tab made (one it sent
// a PUT for, or the one the entry's own bytes make) and the server lacks
// it: the pagehide flush sends the block's PUT beside the upload, naming
// the file by its hash (docs/dev/handwriting.md "Fences"), and the PUT
// landed while the upload did not (a group past the flush's keepalive
// budget goes as plain fetches, and the closing page may cut off the
// upload and not the PUT). Then the entry is that drawing, whole.
async function lostUpload(url, entry) {
  if (typeof entry?.bytes !== "string") return false;
  if (!(Array.isArray(entry.sent) && entry.sent.includes(url))) {
    try { if ((await uploadName(entry.bytes)) !== url) return false; } catch { return false; }
  }
  return !!(await fetchFile(url)).gone;
}
// One entry restored into its block; → "again" when the block's file
// changed while it ran.
async function restore(block, { key, entry }) {
  if (await tabAlive(entry?.tab)) return null;
  const url = blockUrl(block);
  const current = url ? await loadInk(url) : null;
  const lost = !!url && !current && await lostUpload(url, entry);
  if (url && !current && !lost) return null;   // the block's file did not load: leave the entry for the next visit
  // What the entry's change counts from: the newest upload its tab sent a
  // PUT for that the server has (that PUT may have landed unanswered), else
  // the file its draft was edited from. A lost upload comes back whole, and
  // its save uploads it again.
  let baseUrl = entry?.base || "", base = null;
  if (!lost) {
    for (const sent of [...(Array.isArray(entry?.sent) ? entry.sent : [])].reverse()) {
      if (typeof sent !== "string" || !sent) continue;
      if (sent === url) { baseUrl = url; break; }
      const got = await fetchFile(sent);
      if (got.error) return null;                // not known to be gone: the next visit tries again
      if (got.ink) { baseUrl = sent; base = got.ink; break; }
    }
    if (!base && baseUrl && baseUrl !== url) {
      const got = await fetchFile(baseUrl);
      if (got.error) return null;
      base = got.ink || null;
    }
  }
  const result = journal.restoredInk(entry, { url, current, base, baseUrl, lost });
  if (!result) { journal.deleteEntry(key); return null; }
  if (drafts.has(block.id)) return null;         // drawn on here meanwhile: the entry waits for the next visit
  if (blockUrl(block) !== url) return "again";   // someone saved meanwhile: against their file, so Discard's prior is it
  drafts.set(block.id, { ink: result.ink, dirty: true, url: "", base: url, baseIds: strokeIds(current), sent: [] });
  // the entry is this tab's now, in one write with the closed tab's going
  holdTabLock();
  journalKeys.delete(block.id);
  journal.moveEntry(key, keyOf(block.id), entryOf(drafts.get(block.id)));
  nameLater(result.ink);
  bump();
  const prior = current || { ...result.ink, strokes: [] };
  const report = { id: block.id, ink: result.ink, prior, ids: result.ids };
  if (restoredListeners.size) for (const fn of restoredListeners) fn(report);
  else restoredQueue.push(report);
  return null;
}

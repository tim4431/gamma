// The draft journal (docs/dev/handwriting.md "Client", inkStore): every
// dirty handwriting draft is copied into IndexedDB (database
// gamma-ink-journal, store drafts, key "<workspace>/<block id>/<tab>")
// until its upload lands, so a tab that closes before a flush lands (a
// crash, a lost connection) leaves the strokes behind for the next visit.
// An entry is {bytes, base, baseIds, at, tab, sent}: the draft's canonical
// bytes (serializeInk), the file it was edited from, that file's stroke
// ids, when it was written, the tab that wrote it, and the uploads whose
// block PUT that tab sent and never heard back from (oldest first). A key
// per tab, so two tabs drawing in one group, or a tab drawing in a group
// before the restore of a closed tab's entry for it ran, never write over
// each other's entry. inkStore writes, deletes and restores entries; this
// module is the storage and the one rule for what an entry brings back
// (restoredInk). Every IndexedDB call is wrapped: a browser without it, or
// a private window, loses only the journal.
import { FORMAT, mergeInk, sameStroke, upgradeV1, withDefaults } from "./ink.js";

const DB = "gamma-ink-journal", STORE = "drafts";
export const JOURNAL_DAYS = 7;
const DAY_MS = 86400000;

export const journalPrefix = (ws) => `${ws || ""}/`;
export const journalKey = (ws, blockId, tab) => `${journalPrefix(ws)}${blockId}/${tab}`;
// The block a key names, from what follows its workspace's prefix
// ("<block id>/<tab>").
export function keyBlock(rest) {
  const i = rest.lastIndexOf("/");
  return i < 0 ? rest : rest.slice(0, i);
}
// Whether an entry is older than JOURNAL_DAYS (or carries no date).
export const expired = (entry, now = Date.now()) => !(now - (entry?.at || 0) <= JOURNAL_DAYS * DAY_MS);

let opening = null;
function openDb() {
  if (opening) return opening;
  opening = new Promise((resolve) => {
    let req = null;
    try { req = globalThis.indexedDB?.open(DB, 1) || null; } catch { req = null; }
    if (!req) { resolve(null); return; }
    req.onupgradeneeded = () => { try { req.result.createObjectStore(STORE); } catch { /* there already */ } };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
  return opening;
}
// One transaction on the store: `fn(store)` issues the requests; → what
// `done(requests)` makes of them once it completes, or `fallback`.
async function transact(mode, fn, done = () => true, fallback = null) {
  const db = await openDb();
  if (!db) return fallback;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, mode);
      const reqs = fn(tx.objectStore(STORE));
      tx.oncomplete = () => { try { resolve(done(reqs)); } catch { resolve(fallback); } };
      tx.onerror = () => resolve(fallback);
      tx.onabort = () => resolve(fallback);
    } catch { resolve(fallback); }
  });
}

export const putEntry = (key, entry) => transact("readwrite", (s) => s.put(entry, key));
export const deleteEntry = (key) => transact("readwrite", (s) => s.delete(key));
// `entry` written under `to` and the entry under `from` deleted, in one
// transaction: a tab taking over a closed tab's entry.
export const moveEntry = (from, to, entry) => transact("readwrite", (s) => (from === to ? [s.put(entry, to)] : [s.put(entry, to), s.delete(from)]));
// The keys that start with `prefix` (a workspace's, "<ws>/") as a key
// range, so no other workspace's entry is read; null where there is no
// IDBKeyRange (every key is read then, and filtered).
function prefixRange(prefix) {
  try { return globalThis.IDBKeyRange?.bound(prefix, `${prefix}\uffff`) ?? null; } catch { return null; }
}
// The entries whose key starts with `prefix` → Map(key → entry). One older
// than JOURNAL_DAYS is deleted as it is found, never returned: an entry
// whose block never comes back, or whose file never loads again, goes
// with time.
export async function readEntries(prefix, now = Date.now()) {
  const range = prefixRange(prefix);
  const found = await transact("readonly", (s) => [s.getAllKeys(range), s.getAll(range)], ([keys, values]) => {
    const out = new Map();
    keys.result.forEach((key, i) => { if (String(key).startsWith(prefix)) out.set(String(key), values.result[i]); });
    return out;
  }, new Map());
  const old = [...found].filter(([, entry]) => expired(entry, now)).map(([key]) => key);
  if (old.length) {
    for (const key of old) found.delete(key);
    transact("readwrite", (s) => old.map((key) => s.delete(key)));
  }
  return found;
}

const sameDrawing = (a, b) => a.strokes.length === b.strokes.length && a.strokes.every((s, i) => sameStroke(s, b.strokes[i]));

// `ink` without what the server deleted since `base`: a stroke `base` had
// that `current` lacks, and a piece cut from one of those that `current`
// does not hold. mergeInk lets an edit beat a delete, which suits two
// writers at work; an entry is a tab that is gone, and a stroke deleted
// after it last saved stays deleted, whatever the entry did to it.
function withoutDeleted(ink, base, current) {
  const kept = new Set(current.strokes.map((s) => s.id));
  const gone = new Set(base.strokes.filter((s) => !kept.has(s.id)).map((s) => s.id));
  if (!gone.size) return ink;
  const strokes = ink.strokes.filter((s) => !gone.has(s.id) && !(s.from != null && gone.has(s.from) && !kept.has(s.id)));
  return strokes.length === ink.strokes.length ? ink : { ...ink, strokes };
}

// What an entry brings back for its block, or null when it brings nothing
// (and goes). `url` is the block's ink_url now and `current` that file
// (null for no file). `baseUrl` is the file the entry's change counts
// from: the newest of the entry's `sent` uploads that the server has (its
// PUT may have landed unanswered, so what it carried is the server's now,
// to keep or delete), else the entry's own base; `base` is that file when
// it is not `url` (null when it is gone). `lost`: `url` is an upload the
// entry's tab made, which the server lacks (inkStore's lostUpload). The
// entry as it is when the block still names the file it counts from, or
// names that lost upload; else merged three ways (mergeInk(base, entry,
// current)) less every stroke the server deleted since the base, so
// nothing deleted comes back; nothing when the base is gone, the entry is
// older than JOURNAL_DAYS, cannot be read, or holds the drawing the block
// has. → {ink, ids}: the drawing to make the block's draft, and the
// strokes it adds to the current file.
export function restoredInk(entry, { url = "", current = null, base = null, baseUrl = entry?.base || "", lost = false, now = Date.now() } = {}) {
  if (!entry || typeof entry.bytes !== "string" || expired(entry, now)) return null;
  let ink;
  try { ink = withDefaults(upgradeV1(JSON.parse(entry.bytes))); } catch { return null; }
  if (ink?.format !== FORMAT || !Array.isArray(ink.strokes)) return null;
  const here = current || { ...ink, strokes: [] };
  let out;
  if (lost || (baseUrl || "") === (url || "")) out = ink;
  else if (base) out = withoutDeleted(mergeInk(base, ink, here).ink, base, here);
  else return null;
  if (sameDrawing(out, here)) return null;
  const known = new Set(here.strokes.map((s) => s.id));
  return { ink: out, ids: out.strokes.filter((s) => !known.has(s.id)).map((s) => s.id) };
}

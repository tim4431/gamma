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
// back as version 1. Plain module state with a version counter — React
// subscribes through useInkVersion (ink/InkLayer.jsx).
import { API, apiJson, sha256 } from "../shared/lib/utils";
import { mergeInk, sameStroke, serializeInk, upgradeV1, withDefaults } from "./ink.js";

const files = new Map();     // url → ink | null (null: fetch failed)
const loading = new Map();   // url → Promise
const drafts = new Map();    // block id → {ink, dirty, url, base, baseIds}
const listeners = new Set();
let version = 0;

function bump() {
  version += 1;
  for (const fn of listeners) fn(version);
}
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function currentVersion() { return version; }

const strokeIds = (ink) => (ink?.strokes || []).map((s) => s.id);

// A file fetched again (a merge's result, a reload) with the strokes of
// `known` it still has as the same objects, so the per-object caches
// (decoded samples, outlines, bounds) are reused instead of rebuilt.
export function adoptStrokes(ink, known) {
  if (!ink?.strokes?.length || !known?.strokes?.length) return ink;
  const byId = new Map(known.strokes.map((s) => [s.id, s]));
  let reused = false;
  const strokes = ink.strokes.map((s) => {
    const k = byId.get(s.id);
    if (k && k !== s && sameStroke(k, s)) { reused = true; return k; }
    return s;
  });
  return reused ? { ...ink, strokes } : ink;
}

export function loadInk(url) {
  if (!url) return Promise.resolve(null);
  if (files.has(url)) return Promise.resolve(files.get(url));
  if (loading.has(url)) return loading.get(url);
  const p = apiJson(`${API}${url.replace(/^\/api/, "")}`)
    .then((ink) => { files.set(url, ink && ink.format === "gamma-ink" ? withDefaults(upgradeV1(ink)) : null); return files.get(url); })
    .catch(() => { files.set(url, null); return null; })
    .finally(() => { loading.delete(url); bump(); });
  loading.set(url, p);
  return p;
}

// The strokes to show for a block: its draft, else its file (fetch kicked
// off when unseen — the caller re-renders on the store's next bump).
export function inkFor(block) {
  const url = block?.properties?.ink_url || "";
  const d = drafts.get(block?.id);
  if (d) {
    if (!d.dirty && d.url !== url) { drafts.delete(block.id); }
    else return d.ink;
  }
  if (!url) return null;
  if (!files.has(url)) { loadInk(url); return null; }
  return files.get(url);
}

export function draft(id) { return drafts.get(id) || null; }
// `base`: the ink_url the strokes were edited from, for a block that has no
// draft yet (a draft keeps the base it started from, and the ids of the
// strokes that file had as it was loaded here).
export function setDraft(id, ink, base = "") {
  const prev = drafts.get(id);
  drafts.set(id, prev
    ? { ...prev, ink, dirty: true }
    : { ink, dirty: true, url: "", base, baseIds: base && files.get(base) ? strokeIds(files.get(base)) : [] });
  nameLater(ink);
  bump();
}
// The upload a draft would be — its bytes and the name the server gives
// them (`uploads/<sha24>.ink`, storage.content_digest) — worked out while
// the page is idle, so the flush on pagehide can send the block's PUT
// beside the upload instead of after it: nothing is sure to run once the
// page is gone. A draft edited again before its turn is skipped.
const DIGEST_CHARS = 24;
const named = new WeakMap();   // ink → {body, url}
const idle = typeof requestIdleCallback === "function"
  ? (fn) => requestIdleCallback(fn, { timeout: 1000 }) : (fn) => setTimeout(fn, 50);
function nameLater(ink) {
  idle(() => {
    if (named.has(ink) || ![...drafts.values()].some((d) => d.ink === ink)) return;
    const body = serializeInk(ink);
    sha256(body).then((hash) => named.set(ink, { body, url: `/api/uploads/${hash.slice(0, DIGEST_CHARS)}.ink` }))
      .catch(() => {});
  });
}
// The upload of `ink` landed at `url` and the block now names `stored`:
// the same file, or — another writer changed the drawing meanwhile — the
// server's merge of both. Strokes added meanwhile keep the draft dirty,
// based on `url`, the file they were drawn onto; once the merged file is
// here it replaces the draft, with those strokes merged onto it.
export function markSaved(id, ink, url, stored = url) {
  const d = drafts.get(id);
  if (!d) return;
  files.set(url, ink);
  drafts.set(id, { ...d, url: stored, base: url, baseIds: strokeIds(ink), dirty: d.ink !== ink });
  bump();
  if (stored === url) return;
  loadInk(stored).then((loaded) => {
    const cur = drafts.get(id);
    if (!loaded || !cur || cur.url !== stored) return;
    const merged = adoptStrokes(loaded, cur.ink);
    files.set(stored, merged);
    drafts.set(id, cur.ink === ink
      ? { ink: merged, dirty: false, url: stored, base: stored, baseIds: strokeIds(merged) }
      : { ink: mergeInk(ink, cur.ink, merged).ink, dirty: true, url: stored, base: stored, baseIds: strokeIds(merged) });
    bump();
  });
}
// An acknowledged deletion still masks the old file while its block is in
// the tree. Do not cache the empty ink under the old file URL, or discard
// an edit made while the delete request was pending.
export function markDeleted(id, ink, url) {
  const d = drafts.get(id);
  if (d?.ink === ink) drafts.set(id, { ...d, dirty: false, url, base: url, baseIds: [] });
}
// A block that comes back into the tree after its deletion (an undo of an
// erasure, a cut, a merge) is a fresh block: its next draft starts from no
// base. Left in place, the deleted draft's base (the old file) would make
// the server merge the re-upload three ways against the new block's empty
// drawing, and the strokes brought back would count as deleted there.
export function dropDraft(id) {
  drafts.delete(id);
}
// `upload`: the draft's bytes and name when they are worked out already
// (nameLater), else null.
export function dirtyDrafts() {
  return [...drafts.entries()].filter(([, d]) => d.dirty)
    .map(([id, d]) => ({ id, ink: d.ink, base: d.base, baseIds: d.baseIds, upload: named.get(d.ink) || null }));
}

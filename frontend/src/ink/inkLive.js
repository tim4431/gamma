// Live co-drawing (docs/dev/handwriting.md "Wet strokes", docs/dev/collab.md
// "ink-live"): a stroke while it is written travels to the page's other
// peers as `ink-live` chunks over the page socket, and theirs arrive here.
// This module is both halves, pure: the chunk a writer sends (liveChunk),
// the writer's ledger of strokes sent and not saved yet (liveLedger), and
// the receiving side's WET strokes, by surface (a PDF page number, a
// sheet's block id). Plain module state like inkStore: a surface's layer
// subscribes to its own wet strokes and paints them on a canvas of its own,
// so a peer's chunk repaints that one canvas and re-renders nothing. A wet
// stroke goes when a loaded file on its surface holds its id (the layer
// reports it, `landWet`), on the writer's `drop`, when the writer leaves
// the page (`keepWetOf`), and WET_MS after its last chunk.

export const LIVE_MS = 50;         // a writer sends a chunk at most this often
export const LIVE_SAMPLES = 64;    // samples per chunk at most (the server relays no more)
export const WET_MS = 60000;       // a wet stroke without news goes after this
const MAX_WET_SAMPLES = 20000;     // one wet stroke holds at most this many

const r2 = (v) => Math.round(v * 100) / 100;
const r3 = (v) => Math.round(v * 1000) / 1000;
const finite = (v) => typeof v === "number" && Number.isFinite(v);
const WORD = /^[a-z][a-z0-9-]{0,15}$/;

// --- sending -----------------------------------------------------------------

// The look a stroke in progress is sent with: the preset's, as the
// committed stroke will be encoded (`brush`, a calligraphy pen's `nib` and
// `dash` only when set).
export function liveStyle(use, pen) {
  return {
    tool: use.tool || "pen", size: use.size, color: use.color, opacity: use.opacity ?? 1, pen: pen !== false,
    ...(use.brush ? { brush: use.brush } : {}), ...(use.brush === "calligraphy" && nibOk(use.nib) ? { nib: use.nib } : {}),
    ...(use.dash ? { dash: use.dash } : {}),
  };
}
const nibOk = (v) => Number.isInteger(v) && v >= 0 && v <= 179;

// Samples `from` … (at most LIVE_SAMPLES of them) as a chunk's `samples`:
// [x, y, p, t, a, z] with page points to two decimals, the pressure 0..1
// (null without one: `pen` false), ms since the stroke's first sample, and
// the altitude and azimuth in integer degrees (null when not reported).
export function liveSamples(samples, from, pen = true) {
  const out = [];
  for (let i = from; i < samples.length && out.length < LIVE_SAMPLES; i++) {
    const s = samples[i];
    out.push([r2(s.x), r2(s.y), pen && finite(s.p) ? r3(s.p) : null, Math.round(s.t || 0),
      finite(s.a) ? Math.round(s.a) : null, finite(s.z) ? Math.round(s.z) : null]);
  }
  return out;
}

// The writer's strokes whose chunks went out and that no saved file holds
// yet, id → surface. One taken off its group before a save holds it (an
// undo, an erasure, a cut or a delete right after the lift) is a stroke no
// file will ever land on the peers' pages, so its wet stroke would stay
// there until WET_MS: `removed(changes)` → the drops to send for it,
// [{id, page}], once each, for the strokes `changes` ({before, after}
// drawings, one edit across groups) take away from every group; a stroke
// that only moves to another group stays. `sent(id, page, drop)` notes a
// chunk sent (a `drop` forgets the stroke), `saved(ink)` the strokes a
// saved file holds, `clear()` everything (leaving the page).
export function liveLedger() {
  const live = new Map();
  return {
    sent(id, page, drop = false) { if (drop) live.delete(id); else if (id) live.set(id, page); },
    saved(ink) { for (const s of ink?.strokes || []) live.delete(s.id); },
    removed(changes) {
      if (!live.size) return [];
      const kept = new Set();
      for (const c of changes || []) for (const s of c.after?.strokes || []) kept.add(s.id);
      const out = [];
      for (const c of changes || []) {
        for (const s of c.before?.strokes || []) {
          if (kept.has(s.id) || !live.has(s.id)) continue;
          out.push({ id: s.id, page: live.get(s.id) });
          live.delete(s.id);
        }
      }
      return out;
    },
    clear() { live.clear(); },
  };
}

// --- receiving ---------------------------------------------------------------

const wet = new Map();        // surface → Map("client/id" → wet stroke)
const listeners = new Map();  // surface → Set(fn)

function notify(surface) {
  for (const fn of listeners.get(surface) || []) fn();
}
function remove(surface, key) {
  const here = wet.get(surface);
  const held = here?.get(key);
  if (!held) return false;
  clearTimeout(held.timer);
  here.delete(key);
  if (!here.size) wet.delete(surface);
  return true;
}
const plainStyle = (s) => {
  if (!s || typeof s !== "object" || !finite(s.size) || s.size <= 0 || s.size > 100) return null;
  const tool = typeof s.tool === "string" && WORD.test(s.tool) ? s.tool : "pen";
  return {
    tool, size: s.size, color: typeof s.color === "string" && s.color.length <= 40 ? s.color : "#1f1f1f",
    opacity: finite(s.opacity) && s.opacity > 0 && s.opacity <= 1 ? s.opacity : 1, pen: s.pen !== false,
    ...(typeof s.brush === "string" && WORD.test(s.brush) ? { brush: s.brush } : {}),
    ...(nibOk(s.nib) ? { nib: s.nib } : {}),
    ...(typeof s.dash === "string" && WORD.test(s.dash) ? { dash: s.dash } : {}),
  };
};
// A chunk's samples as the layer's samples ({x, y, p?, t, a?, z?}); a
// malformed one is skipped.
function decodeSamples(list, last) {
  const out = [];
  let t = last?.t ?? 0;
  for (const q of Array.isArray(list) ? list : []) {
    if (!Array.isArray(q) || !finite(q[0]) || !finite(q[1])) continue;
    t = finite(q[3]) ? Math.max(t, q[3]) : t;
    const s = { x: q[0], y: q[1], t };
    if (finite(q[2])) s.p = Math.max(0, Math.min(1, q[2]));
    if (finite(q[4])) s.a = q[4];
    if (finite(q[5])) s.z = q[5];
    out.push(s);
  }
  return out;
}

// An `ink-live` message from the page socket (`client` added by the
// server). The first chunk (`n` 0) starts a wet stroke; a later one adds
// its samples when its `n` is the count held, and a gap (a chunk missed)
// drops the stroke: nothing more is drawn of it until its file lands.
// `drop` cancels it. → whether anything changed.
export function receiveInkLive(msg, now = Date.now()) {
  if (!msg || typeof msg.client !== "string" || typeof msg.id !== "string") return false;
  const surface = msg.page;
  if (!(finite(surface) || (typeof surface === "string" && surface))) return false;
  const key = `${msg.client}/${msg.id}`;
  const held = wet.get(surface)?.get(key) || null;
  if (msg.drop) {
    const gone = remove(surface, key);
    if (gone) notify(surface);
    return gone;
  }
  const n = Number.isInteger(msg.n) ? msg.n : 0;
  if (n !== (held ? held.samples.length : 0)) {
    const gone = remove(surface, key);
    if (gone) notify(surface);
    return gone;
  }
  let stroke = held;
  if (!stroke) {
    const style = plainStyle(msg.style);
    if (!style) return false;
    stroke = { key, client: msg.client, id: msg.id, surface, style, k: finite(msg.k) ? msg.k : 1, samples: [], done: false };
    if (!wet.has(surface)) wet.set(surface, new Map());
    wet.get(surface).set(key, stroke);
  }
  const add = decodeSamples(msg.samples, stroke.samples.at(-1));
  if (stroke.samples.length + add.length > MAX_WET_SAMPLES) add.length = Math.max(0, MAX_WET_SAMPLES - stroke.samples.length);
  // Replaced, never mutated: a painter holding the old list keeps its frame.
  stroke.samples = add.length ? [...stroke.samples, ...add] : stroke.samples;
  stroke.g = typeof msg.g === "string" ? msg.g : stroke.g ?? null;
  stroke.done = stroke.done || !!msg.done;
  stroke.at = now;
  clearTimeout(stroke.timer);
  stroke.timer = setTimeout(() => { if (remove(surface, key)) notify(surface); }, WET_MS);
  stroke.timer?.unref?.();
  notify(surface);
  return true;
}

// The wet strokes on a surface, in the order they began.
export function wetOn(surface) {
  const here = wet.get(surface);
  return here ? [...here.values()] : [];
}
// fn() on every change of a surface's wet strokes; → unsubscribe.
export function subscribeWet(surface, fn) {
  if (!listeners.has(surface)) listeners.set(surface, new Set());
  listeners.get(surface).add(fn);
  return () => {
    const set = listeners.get(surface);
    set?.delete(fn);
    if (set && !set.size) listeners.delete(surface);
  };
}
// The committed strokes `ids` are drawn on the surface now: their wet
// strokes go (the file replaced them, so nothing changes on screen).
export function landWet(surface, ids) {
  const here = wet.get(surface);
  if (!here) return false;
  const landed = new Set(ids);
  let changed = false;
  for (const s of [...here.values()]) if (landed.has(s.id)) changed = remove(surface, s.key) || changed;
  if (changed) notify(surface);
  return changed;
}
// Only these peers (client ids) are on the page: everyone else's wet
// strokes go — a `leave`, or the socket's close, which empties the peers.
export function keepWetOf(clients) {
  const keep = clients instanceof Set ? clients : new Set(clients || []);
  for (const [surface, here] of [...wet]) {
    let changed = false;
    for (const s of [...here.values()]) if (!keep.has(s.client)) changed = remove(surface, s.key) || changed;
    if (changed) notify(surface);
  }
}
// Every wet stroke goes (leaving the page).
export function clearWet() {
  for (const [surface, here] of [...wet]) {
    for (const s of here.values()) clearTimeout(s.timer);
    wet.delete(surface);
    notify(surface);
  }
}

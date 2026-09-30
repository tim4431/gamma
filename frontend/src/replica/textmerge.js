// The same-block text merge: the JavaScript twin of gamma/textmerge.py, for
// a client that applies another side's text changes itself — the iPad's
// replica (replica/tree.js), where no server merges for it. Same rules,
// same diff (Google's diff-match-patch, the same algorithm in both
// languages); tests/shared/textmerge.json pins both.
//
// A change base → text is a list of hunks (a span of base replaced by new
// text; a pure insertion replaces an empty span), and both sides' hunks
// are applied to base together: edits to different spans both survive,
// and so do two insertions at one offset (the stored one first). Only a
// hunk of ours that replaces characters theirs replaced too is dropped.
//
// Offsets are UTF-16 code units here and code points in Python: the two
// agree on every text without characters outside the BMP (emoji).
import DiffMatchPatch from "diff-match-patch";

const dmp = new DiffMatchPatch();
dmp.Diff_Timeout = 0.2; // seconds before the diff settles for a coarser answer, as in Python

const diff = (a, b, checklines = false) => dmp.diff_main(a, b, checklines).map((d) => [d[0], d[1]]);

// The change base → text as [start, end, insert] hunks in base offsets.
function hunks(base, text) {
  const out = [];
  let at = 0;
  for (const [kind, chunk] of diff(base, text)) {
    if (kind === 0) { at += chunk.length; continue; }
    let [start, end, insert] = kind > 0 ? [at, at, chunk] : [at, at + chunk.length, ""];
    if (out.length && out[out.length - 1][1] === start) { // a delete and an insert side by side: one hunk
      const [first, , before] = out.pop();
      start = first;
      insert = before + insert;
    }
    out.push([start, end, insert]);
    at = end;
  }
  return out;
}

const cmp = (a, b) => {
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
};

// Merge the change base → ours into theirs (the text stored now). →
// [text, clean]; clean is false when a hunk of ours was dropped.
export function merge(base, ours, theirs) {
  if (theirs === base) return [ours, true];
  if (ours === base || ours === theirs) return [theirs, true];
  const stored = hunks(base, theirs), mine = hunks(base, ours);
  const kept = [];
  let j = 0;
  for (const [start, end, insert] of mine) {
    while (j < stored.length && stored[j][1] <= start) j++;
    let clash = false;
    for (let k = j; k < stored.length; k++) {
      const [s, e] = stored[k];
      if (s >= end) break;
      if (Math.max(start, s) < Math.min(end, e)) { clash = true; break; }
    }
    if (!clash) kept.push([start, end, insert]);
  }
  // Both lists in base order: at one offset a pure insertion before a
  // replacement, and of two insertions theirs first (Python's tuple sort).
  const all = [...stored.map(([s, e, i]) => [s, e > s ? 1 : 0, 0, e, i]), ...kept.map(([s, e, i]) => [s, e > s ? 1 : 0, 1, e, i])]
    .sort(cmp);
  const out = [];
  let at = 0;
  for (const [start, , , end, insert] of all) {
    if (start > at) { out.push(base.slice(at, start)); at = start; }
    out.push(insert);
    at = Math.max(at, end);
  }
  out.push(base.slice(at));
  return [out.join(""), kept.length === mine.length];
}

function edits(base, text) {
  const inserted = new Map(), deleted = new Set();
  let at = 0;
  for (const [kind, chunk] of diff(base, text)) {
    if (kind > 0) { inserted.set(at, (inserted.get(at) || "") + chunk); continue; }
    if (kind < 0) for (let i = at; i < at + chunk.length; i++) deleted.add(i);
    at += chunk.length;
  }
  return { inserted, deleted };
}

// Whether theirs already holds the change base → ours (merging it again
// would double it).
export function contains(base, ours, theirs) {
  const o = edits(base, ours), t = edits(base, theirs);
  for (const i of o.deleted) if (!t.deleted.has(i)) return false;
  for (const [at, s] of o.inserted) if (!(t.inserted.get(at) || "").includes(s)) return false;
  return true;
}

// Where the caret at `offset` in src sits in dst.
export function mapOffset(src, dst, offset) {
  if (src === dst) return offset;
  const at = Math.max(0, Math.min(Math.trunc(offset), src.length));
  return dmp.diff_xIndex(dmp.diff_main(src, dst), at);
}

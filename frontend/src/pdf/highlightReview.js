// The arithmetic of the review card (pdf/HighlightReview.jsx,
// docs/dev/highlight_relocation.md): after a page's PDF was replaced, a
// highlight the run drew dashed (pdf_position.approx, not lost) is pending
// until the person accepts or moves it, and its position keeps where it was
// before (pdf_position.prev: {doc_id, pageNumber, rects, quote}, the rects
// as [x0, y0, x1, y1] fractions of that page as shown, top-left origin).
// One the run could not place is lost, drawn hollow where it was, and may
// keep closest, the best place it found below the account's threshold.
// Both are reviewed on the card, one queue in document order. This module
// says which highlights are to review, in that order, and which comes
// next; what the card shows for one (reviewCardState) and what its steps
// change, for their Undo (reviewStep); the box the before picture is cut
// from, and where the old rectangles fall in it; and the word comparison
// of the old quote with the current one. Pure, so node tests it
// (tests/highlightReview.test.mjs).
import { acceptedPosition, closestPlace, closestPlacement, placeToReview, placedToCheck, roughMatchPercent, sameJson } from "../shared/model/blockModel.js";
import { tokens, wordDiff } from "../shared/lib/wordDiff.js";

// --- Which highlights are to review, in document order ---------------------

// A highlight's place in reading order: its page, then the top and the left
// of its box as fractions of the page.
function orderKey(h) {
  const pos = h.position || {};
  const r = pos.boundingRect || {};
  return [pos.pageNumber || 0, (r.y1 || 0) / (pos.height || 1), (r.x1 || 0) / (pos.width || 1)];
}
const compareKeys = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

function inOrder(highlights, keep) {
  return (highlights || [])
    .filter((h) => keep(h?.position))
    .map((h) => [orderKey(h), h])
    .sort(([a], [b]) => compareKeys(a, b))
    .map(([, h]) => h);
}

// The page's pending highlights (approx and not lost, dashed), in document
// order: the Document menu's Accept all.
export function pendingInOrder(highlights) {
  return inOrder(highlights, placedToCheck);
}

// The page's highlights to review, the pending (dashed) and the lost
// (hollow) ones, in document order: what the card's Next walks, its
// "{i} of {n}" counts and the Document menu's Review opens the first of.
export function reviewQueue(highlights) {
  return inOrder(highlights, placeToReview);
}

// Where the highlight `at` (a highlight, or its id) stands in `queue`
// (reviewQueue): its index (-1 when it is not in it), how many are left,
// and the one Next goes to. For one in the queue, the one after it, the
// first again after the last, or null when it is the only one. For one
// resolved meanwhile (accepted, placed, moved), so out of the queue, the
// first at or after where it stood in document order, again the first
// after the last: where it stood is the position `done` (reviewStep's, the
// step the card took on it) found it at, since Use closest match or a move
// takes it elsewhere, else its place now; null when nothing is left, or
// for an id alone.
export function reviewPlace(queue, at, done = null) {
  const id = typeof at === "string" ? at : at?.id;
  const index = queue.findIndex((h) => h.id === id);
  const total = queue.length;
  if (index >= 0) return { index, total, next: total > 1 ? queue[(index + 1) % total] : null };
  const stood = done?.before?.position || at?.position;
  if (!total || !stood) return { index, total, next: null };
  const key = orderKey({ position: stood });
  return { index, total, next: queue.find((h) => compareKeys(orderKey(h), key) >= 0) || queue[0] };
}

// Whether a highlight's page is past the last page of the PDF shown
// (`pageCount`, 0 while it is not known): one lost from a page the new
// version dropped. Nothing draws it, so its review card stands in the
// viewer instead, saying so.
export function pastLastPage(pos, pageCount) {
  return pageCount > 0 && Number.isInteger(pos?.pageNumber) && pos.pageNumber > pageCount;
}

// --- Where the highlight was before ----------------------------------------

const isFrac = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
const validRect = (r) => Array.isArray(r) && r.length === 4 && r.every(isFrac) && r[0] < r[2] && r[1] < r[3];

// The longest quote a prev keeps (gamma/highlights.py PREV_MAX_QUOTE): a
// longer one is kept as its first PREV_QUOTE_MAX characters, cut wherever
// they end, inside a word too.
export const PREV_QUOTE_MAX = 1000;

// The position's prev, read defensively: {docId, page, rects, quote,
// clipped}, its docId "" when it names no stored file (the card then shows
// the quote alone), clipped when the quote is as long as a prev keeps (so
// it may be the start of a longer one); or null when the position keeps
// none.
export function reviewPrev(pos) {
  const prev = pos?.prev;
  if (!prev || typeof prev !== "object" || Array.isArray(prev)) return null;
  const quote = typeof prev.quote === "string" ? prev.quote : "";
  return {
    docId: typeof prev.doc_id === "string" && /^[0-9a-f]+$/.test(prev.doc_id) ? prev.doc_id : "",
    page: Number.isInteger(prev.pageNumber) && prev.pageNumber > 0 ? prev.pageNumber : 0,
    rects: Array.isArray(prev.rects) ? prev.rects.filter(validRect) : [],
    quote,
    clipped: quote.length >= PREV_QUOTE_MAX,
  };
}

// The rectangles a lost highlight keeps, as page fractions (reviewPrev's
// shape), at most as many as a prev keeps.
const PREV_MAX_RECTS = 200;
function fractionRects(pos) {
  const w = pos?.width, ht = pos?.height;
  if (!(w > 0) || !(ht > 0) || !Array.isArray(pos.rects)) return [];
  return pos.rects
    .filter((r) => r && [r.x1, r.y1, r.x2, r.y2].every((v) => typeof v === "number" && Number.isFinite(v)))
    .map((r) => [r.x1 / w, r.y1 / ht, r.x2 / w, r.y2 / ht].map((v) => round4(clamp01(v))))
    .filter(validRect)
    .slice(0, PREV_MAX_RECTS);
}

// Where a lost highlight was, in reviewPrev's shape plus `current`, from
// its own position: a lost one keeps its rectangles and the file they were
// measured on (doc_id). When that names a file other than the page's
// current one (`fileId`), the before picture is cut from it; when it names
// none, or the current file, the rectangles are this file's, drawn hollow
// right there, so `current` is true and docId "": its wording alone. The
// quote is its stored one, whole.
export function lostPlace(h, fileId = "") {
  const pos = h?.position || {};
  const named = typeof pos.doc_id === "string" ? pos.doc_id : "";
  const current = !named || named === fileId;
  return {
    docId: !current && /^[0-9a-f]+$/.test(named) ? named : "",
    page: Number.isInteger(pos.pageNumber) && pos.pageNumber > 0 ? pos.pageNumber : 0,
    rects: fractionRects(pos),
    quote: typeof h?.content?.text === "string" ? h.content.text : "",
    clipped: false,
    current,
  };
}

// A page's height over its width, for a page whose size is not known
// (US Letter; A4 is 1.414).
export const PAGE_ASPECT = 792 / 612;
// A line of body text as a fraction of the page's height, for an area,
// whose one rectangle says nothing about the text's size; a text
// highlight's own rectangles, one per line, give it otherwise, kept in
// these bounds.
const LINE = 0.014, LINE_MIN = 0.006, LINE_MAX = 0.03;
// The margin around the old rectangles: about two lines of text with their
// leading, the same width across in points as down.
const MARGIN_LINES = 2.4;

const round4 = (v) => Math.round(v * 10000) / 10000;
const clamp01 = (v) => Math.min(1, Math.max(0, v));

// The box ([x0, y0, x1, y1] page fractions, four decimals) the before
// picture is cut from (GET /api/ai/selection-crop's `box`): the union of
// the old rectangles widened by MARGIN_LINES lines of text, kept on the
// page. `pageAspect` is the page's height over its width; null when there
// is no rectangle to show.
export function cropBox(rects, { area = false, pageAspect = PAGE_ASPECT } = {}) {
  const list = (rects || []).filter(validRect);
  if (!list.length) return null;
  const heights = list.map((r) => r[3] - r[1]).sort((a, b) => a - b);
  const line = area ? LINE : Math.min(LINE_MAX, Math.max(LINE_MIN, heights[Math.floor(heights.length / 2)]));
  const my = MARGIN_LINES * line;
  const mx = my * (pageAspect > 0 ? pageAspect : PAGE_ASPECT);
  const box = [
    round4(clamp01(Math.min(...list.map((r) => r[0])) - mx)),
    round4(clamp01(Math.min(...list.map((r) => r[1])) - my)),
    round4(clamp01(Math.max(...list.map((r) => r[2])) + mx)),
    round4(clamp01(Math.max(...list.map((r) => r[3])) + my)),
  ];
  return box[0] < box[2] && box[1] < box[3] ? box : null;
}

// The before picture's request for `prev` (reviewPrev's): {box, path},
// path being GET /api/ai/selection-crop's under /api, or null when there
// is none to ask for: no stored file named, no page or rectangle, or a
// share view (`shared`). The route is a workspace member's: it reads no
// share token, and its box is the asker's to choose, so it is not opened
// to a share link, which would let one cut whole pages of a file the owner
// replaced; the card shows the old quote instead.
export function beforeCrop(prev, { area = false, pageAspect = PAGE_ASPECT, shared = false } = {}) {
  if (!prev?.docId || !prev.page || shared) return null;
  const box = cropBox(prev.rects, { area, pageAspect });
  return box ? { box, path: `/ai/selection-crop/${prev.docId}?page=${prev.page}&box=${box.join(",")}` } : null;
}

// The old rectangles in the picture cut from `box`: {left, top, width,
// height} as fractions of the picture, each kept inside it.
export function rectsInCrop(rects, box) {
  if (!box) return [];
  const [bx0, by0, bx1, by1] = box;
  const w = bx1 - bx0, h = by1 - by0;
  const out = [];
  for (const r of (rects || []).filter(validRect)) {
    const x0 = Math.max(r[0], bx0), y0 = Math.max(r[1], by0), x1 = Math.min(r[2], bx1), y1 = Math.min(r[3], by1);
    if (x1 > x0 && y1 > y0) out.push({ left: (x0 - bx0) / w, top: (y0 - by0) / h, width: (x1 - x0) / w, height: (y1 - y0) / h });
  }
  return out;
}

// The picture's width over its height, for its placeholder while it loads
// (the page's own proportions are known only once it has).
export function cropAspect(box, pageAspect = PAGE_ASPECT) {
  if (!box) return 1;
  return (box[2] - box[0]) / (box[3] - box[1]) / (pageAspect > 0 ? pageAspect : PAGE_ASPECT);
}

// --- The old quote against the current one ---------------------------------

// Words of an unchanged stretch kept beside a change; the rest of it is
// cut to an ellipsis, so a long passage shows what changed at a glance.
export const DIFF_CONTEXT = 6;

// The first or the last `n` words of `toks` (tokens: words and spaces).
function wordsOf(toks, n, fromEnd) {
  let count = 0;
  if (!fromEnd) {
    let i = 0;
    for (; i < toks.length && count < n; i++) if (/\S/.test(toks[i])) count++;
    return toks.slice(0, i).join("");
  }
  let i = toks.length;
  for (; i > 0 && count < n; i--) if (/\S/.test(toks[i - 1])) count++;
  return toks.slice(i).join("");
}

// A long unchanged stretch cut down to the words beside its changes: the
// first part keeps its end, the last its start, one between keeps both.
function elide(parts, keep) {
  if (!parts.some((p) => p.tag !== "same")) return parts;
  return parts.map((p, i) => {
    if (p.tag !== "same") return p;
    const toks = tokens(p.text);
    const words = toks.filter((s) => /\S/.test(s)).length;
    const first = i === 0, last = i === parts.length - 1;
    if (first && words > keep) return { ...p, text: `… ${wordsOf(toks, keep, true)}` };
    if (last && words > keep) return { ...p, text: `${wordsOf(toks, keep, false)} …` };
    if (!first && !last && words > 2 * keep + 1) {
      return { ...p, text: `${wordsOf(toks, keep, false)} … ${wordsOf(toks, keep, true)}` };
    }
    return p;
  });
}

const wordCount = (text) => tokens(text).filter((s) => /\S/.test(s)).length;

// The old quote against the current one, word by word: [{text, tag}],
// tag "same", "del" (only in the old one: struck through) or "add" (only
// in the current one: marked), whitespace read as one space, unchanged
// stretches cut to DIFF_CONTEXT words beside a change. Null when both are
// empty. The common start and end are matched first, so a long passage
// with one changed citation costs little.
//
// `clipped` (reviewPrev's): the old quote is only the start of the old
// passage, cut wherever PREV_QUOTE_MAX fell. It is compared up to its last
// whole word, and the current quote only as far: what the current one has
// past that point is not marked added (the old one's words there are not
// known), save the words that replace old ones struck at the cut. An
// ellipsis ends the comparison. A change past the cut is not shown.
export function quoteDiff(before, after, { context = DIFF_CONTEXT, clipped = false } = {}) {
  let old = String(before || "").replace(/\s+/g, " ").trim();
  if (clipped) {
    old = old.replace(/\s*\S*$/, "");
    if (!old) return null;
  }
  const a = tokens(old);
  const b = tokens(String(after || "").replace(/\s+/g, " ").trim());
  if (!a.length && !b.length) return null;
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (!clipped && tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const parts = [];
  const push = (text, tag) => {
    if (!text) return;
    const prev = parts[parts.length - 1];
    if (prev && prev.tag === tag) prev.text += text;
    else parts.push({ text, tag });
  };
  push(a.slice(0, head).join(""), "same");
  for (const p of wordDiff(a.slice(head, a.length - tail).join(""), b.slice(head, b.length - tail).join(""))) push(p.text, p.tag);
  push(a.slice(a.length - tail).join(""), "same");
  if (clipped) {
    const past = parts[parts.length - 1]?.tag === "add" ? parts.pop().text : "";
    const struck = parts[parts.length - 1]?.tag === "del" ? wordCount(parts[parts.length - 1].text) : 0;
    if (past && struck) push(wordsOf(tokens(past), struck, false), "add");
    push(/\s$/.test(parts[parts.length - 1]?.text || "") ? "…" : " …", "same");
  }
  return elide(parts, context);
}

// --- What the card shows, and what its steps change -------------------------

// What the review card shows for highlight `h`, `done` the step the card
// took on it (reviewStep's, or null) and `fileId` the stored file the page
// shows now; null when there is nothing to show (the card then closes):
//
// - {kind: "resolved", step}: a step taken on the card ("accepted",
//   "placed" or "moved") left it out of the queue; the card stays, with
//   Undo and Next. One back in the queue (its Undo, the app's undo) reads
//   as what it is again, whatever `done` says.
// - {kind: "pending", percent, area, before, diff}: dashed, matched
//   roughly (`percent`, roughMatchPercent's) or placed by estimate (null);
//   `before` reviewPrev's, `diff` the old quote against the current one
//   (none for an area).
// - {kind: "lost", area, before, closest, diff}: hollow; `before`
//   lostPlace's, or null when it has nothing to show (its wording is the
//   diff's, or there is none); `closest` closestPlace's with its whole
//   `percent`, or null, and then `diff` its stored quote against the
//   candidate's text.
export function reviewCardState(h, { done = null, fileId = "" } = {}) {
  const pos = h?.position;
  if (!pos) return null;
  const area = !!pos.area;
  if (placedToCheck(pos)) {
    const before = reviewPrev(pos);
    return {
      kind: "pending", percent: roughMatchPercent(pos), area, before,
      diff: before && !area ? quoteDiff(before.quote, h.content?.text || "", { clipped: before.clipped }) : null,
    };
  }
  if (pos.lost) {
    const place = lostPlace(h, fileId);
    const found = area ? null : closestPlace(pos);
    const closest = found ? { ...found, percent: Math.round(found.match * 100) } : null;
    // on the current file there is no picture to show: its wording, unless the comparison holds it
    const before = place.current && (closest || !place.quote) ? null : place;
    return { kind: "lost", area, before, closest, diff: closest ? quoteDiff(place.quote, closest.quote) : null };
  }
  if (done) return { kind: "resolved", step: done.kind };
  return null;
}

// What a step on the card changes, for its Undo (blockModel reviewUndone):
// {kind, before: {position, quote?}, after, prior?}, `before` the
// highlight's position (and, for a step that rewrites it, its quote)
// before the step, `after` the position the step writes. "accepted"
// (Accept on a pending one: acceptedPosition) keeps the quote; "placed"
// (Use closest match on a lost one: closestPlacement) and "moved" (a
// selection, `after` its position) take the new text. A move started from
// a card already resolved keeps that card's step as `prior` while the
// position moved from is the one that step wrote, so the move's Undo
// opens the card on it again ("Accepted", with its own Undo). Null when
// the step does not apply.
export function reviewStep(kind, { position, quote = "", prior = null } = {}, after = null) {
  if (!position) return null;
  if (kind === "accepted") return placedToCheck(position) ? { kind, before: { position }, after: acceptedPosition(position) } : null;
  if (kind === "placed") {
    const placed = closestPlacement(position);
    return placed ? { kind, before: { position, quote }, after: placed.position } : null;
  }
  if (kind === "moved") {
    if (!after) return null;
    const step = { kind, before: { position, quote }, after };
    if (prior?.after && sameJson(prior.after, position)) step.prior = prior;
    return step;
  }
  return null;
}

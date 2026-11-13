// Logseq-style block model: each block has id, content, properties, children.
// Highlights (link regions too) are blocks with a properties.pdf_position
// that are no ink group, text box, picture or sheet; the block id is the
// highlight's id. Free notes are the blocks without one. A pdf_position
// (gamma/highlights.py) is {pageNumber, width, height, boundingRect: {x1,
// y1, x2, y2}, rects: [{x1, y1, x2, y2}, …]} (area: true on an area
// highlight): the 1-based page, the page as measured when the place was
// taken, and the rectangles in that frame, top-left origin. After the
// page's PDF is replaced a highlight's position may also carry doc_id (the
// stored file its rectangles were measured on, when that is no longer the
// page's PDF; absent, the current one, which is what a selection here
// writes), lost: true (its text was not found in the new file: drawn
// hollow), and approx: true (drawn dashed, pending until the person accepts
// or moves it: a passage matched roughly, with match, how alike its text
// and the text found read, 0..0.99; or, without match, an area placed by
// estimate or left where it was when nothing placed it). A pending one also
// keeps prev, where it was before the run that first drew it dashed
// ({doc_id, pageNumber, rects: [[x1, y1, x2, y2], …] as fractions of that
// page, quote before it was respelled}), which its review card shows
// (pdf/highlightReview.js) and later runs match it from; one they do not
// find is lost back at that place, naming that file. Accepting it (acceptedPosition) drops approx,
// match, prev and doc_id. A lost one may keep closest, the best place in
// the current PDF the run found for its text, which read too unlike it to
// be applied ({pageNumber, width, height, boundingRect, rects, match,
// quote}, closestPlace), which its card offers to apply (closestPlacement:
// its place and quote, lost, closest and doc_id gone);
// docs/dev/highlight_relocation.md. A highlight whose place on its page is
// not known carries pageNumber alone. A range of a text file is the third
// place (text_position, isTextRangeBlock): {line, col, end, end_col,
// prefix, suffix, hash}, 1-based lines, 0-based code-point columns, the
// text around it and the version it was measured on, always written; a run
// after the file changed writes the same flags beside it (textSpan,
// docs/dev/api.md "The highlight shape").
//
// The tree is the DOCUMENT and nothing else: every change to it is an edit
// the live session sends and the undo history records. What only this
// viewer sees — the block whose editor is open, the folding they changed —
// is a `view` kept beside the tree ({editingId, folds}, EMPTY_VIEW below),
// never a flag on a node, so revealing a block or opening an editor can't
// be mistaken for an edit, and an edit can't be mistaken for a load.
import { fenceInnerAt } from "../../editor/fences.js";
import { findMathAtCursor } from "../../editor/latexCompletion.js";
import { isWholeLine, parseLine, withoutFirstMarker } from "../../editor/mdLines.js";
import { t } from "../i18n/i18n.js";
import { blockStyle, withStyle } from "./markStyle.js";
import { DEFAULT_HIGHLIGHT, normalizeColor } from "./palette.js";
import { wallNow } from "./wallClock.js";

// The one id generator, for blocks and for comments, chat messages,
// uploads and tasks alike (shared/lib/utils.js re-exports it): 9 random
// bytes, base64url — the same shape and strength as the backend's
// secrets.token_urlsafe(9) (12 chars, 72 bits).
export function makeId() {
  const bytes = crypto.getRandomValues(new Uint8Array(9));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_");
}

// --- shape helpers ---

const isObject = (v) => Boolean(v) && typeof v === "object";
// An object that is no array: what the kind rule asks of a text_box,
// picture, sheet or pdf_position (json_type 'object' in gamma/db.py,
// isinstance dict in gamma/blocks_store.py block_kind).
export const isRecord = (v) => isObject(v) && !Array.isArray(v);

// An ink group (docs/dev/handwriting.md "Model"): a block with an `ink_url`
// key, whatever its value — a group whose first file is not saved yet has
// an empty one (ink/inkEdits.js withGroupBlocks). The server's `kind` tests
// it before any other (gamma/blocks_store.py block_kind), by the same rule
// (gamma/highlights.py is_ink).
export const isInkGroup = (b) => b?.properties?.ink_url !== undefined;

// A block that links somewhere: a link_url (a web or document address) or a
// link_page_id (a Gamma page) that is neither missing, null nor empty, the
// server's rule for `kind` link (gamma/blocks_store.py block_kind). A link
// region on the PDF is a highlight too (isHighlightBlock).
const linkTarget = (v) => v != null && v !== "";
export const isLinkBlock = (b) => linkTarget(b?.properties?.link_url) || linkTarget(b?.properties?.link_page_id);

// A picture: no ink group, no text box, a `picture` object
// (markup/picture.js isPicture, which this module cannot import: that would
// be a cycle through notebook/notebook.js).
const placesPicture = (b) => {
  const p = b?.properties;
  return Boolean(p) && !isInkGroup(b) && !isRecord(p.text_box) && isRecord(p.picture);
};

// Where a block's place is stored, by the format of the page's document: a
// PDF's places are pdf_position, a picture's regions image_position, a text
// file's ranges text_position (gamma/highlights.py). positionKeyOf reads a
// document's (pageDocument's view, or null), the PDF's for a page without
// one.
export const POSITION_KEY = Object.freeze({ pdf: "pdf_position", image: "image_position", text: "text_position" });
export const positionKeyOf = (doc) => POSITION_KEY[doc?.format] || POSITION_KEY.pdf;

// A block placed by `key` that no other kind claims: no ink group, text box,
// picture or sheet.
const placedBy = (b, key) => {
  const p = b?.properties;
  return Boolean(p && isRecord(p[key]) && !isInkGroup(b) && !isRecord(p.text_box)
    && !isRecord(p.picture) && !isRecord(p.sheet));
};

// A highlight or a link region on a PDF page: a block with a pdf_position
// that is no ink group, text box, picture or sheet (gamma/highlights.py
// is_highlight; the server's `kind` highlight is wider: isPlaceBlock).
export const isHighlightBlock = (b) => placedBy(b, "pdf_position");

// A region of a picture: a block with an image_position that no other kind
// claims (gamma/highlights.py is_region), the twin of isHighlightBlock on
// an image document. Its shape is pdf_position's without a page:
// {width, height, boundingRect, rects: [one], hash}, in the picture's own
// pixels, `hash` the version it was drawn on.
export const isRegionBlock = (b) => placedBy(b, "image_position");

// A range of a text file: a block with a text_position that no other kind
// claims (gamma/highlights.py is_text_range), the twin of isRegionBlock on
// a text document.
export const isTextRangeBlock = (b) => placedBy(b, "text_position");

// A block with a place on its page's document, a PDF's highlight, a
// picture's region or a text file's range: what the server's `kind` calls a
// highlight, and what the rows, the thread popover and the notes lock treat
// alike.
export const isPlaceBlock = (b) => isHighlightBlock(b) || isRegionBlock(b) || isTextRangeBlock(b);

// A run of a text file's lines in words: "Lines 3–5", "Line 3" for one (a
// range's row, the chat's chip of a selection and its cut note).
export function linesLabel(line, end = line) {
  return end > line ? t("Lines {from}–{to}", { from: line, to: end }) : t("Line {n}", { n: line });
}

// A text range's span {line, col, end, end_col}: whole numbers, `line` from
// 1, `end` not before it, columns from 0 and `end_col` past `col` on a range
// of one line (gamma/highlights.py text_span's rule); null for anything
// else, a malformed position included (nothing validates it on write).
export function textSpan(pos) {
  if (!isRecord(pos)) return null;
  const { line, col, end, end_col } = pos;
  if (![line, col, end, end_col].every(Number.isInteger)) return null;
  if (line < 1 || col < 0 || end < line || end_col < 0 || (end === line && end_col <= col)) return null;
  return { line, col, end, end_col };
}

// Where a comment thread on the block stands on the PDF, {page, position}
// (its pdf_position and that position's page): a highlight or a link region
// (isHighlightBlock), or an ink group drawn on a PDF page. null for any
// other block: a note, a text box, a picture, and a group on a sheet, which
// carries no position.
export function pdfAnchorOf(b) {
  const pos = b?.properties?.pdf_position;
  if (!isRecord(pos) || !(isHighlightBlock(b) || isInkGroup(b))) return null;
  return { page: pos.pageNumber ?? null, position: pos };
}

// Where a comment thread on the block stands on the page's document, by its
// position key (positionKeyOf): a region's {page: null, position} for the
// picture's key, a text range's for the text file's, pdfAnchorOf for any
// other (a stray second argument, a map's index, reads as the PDF's). null
// for a block with no place under that key.
export function anchorOf(b, key = "pdf_position") {
  if (key === POSITION_KEY.image) return isRegionBlock(b) ? { page: null, position: b.properties.image_position } : null;
  if (key === POSITION_KEY.text) return isTextRangeBlock(b) ? { page: null, position: b.properties.text_position } : null;
  return pdfAnchorOf(b);
}

// A plain note: text and nothing else. A block's fold and the moment it was
// made (`t0`) are bookkeeping, not content; any other property makes it
// something more (a highlight, a box, a picture, a sheet, a Zotero note),
// which the editor's text-only behaviour leaves alone.
export const isPlainNote = (b) => Object.keys(b?.properties || {}).every((k) => k === "collapsed" || k === "t0");

// A highlight's colour as stored (#rrggbbaa, palette.js), the default for
// a block without a readable one. An older rgba(…) string reads too.
export function blockColor(b) {
  return normalizeColor(b?.properties?.color) || DEFAULT_HIGHLIGHT;
}

// The PDF page a block is placed on: its position's (a highlight, a link
// region, an ink group), a text box's or a picture's pdf_page (neither
// stores a position: markup/textBox.js, markup/picture.js); null for any
// other block.
export function blockPage(b) {
  const p = b?.properties || {};
  if (isRecord(p.text_box) || placesPicture(b)) return p.pdf_page ?? null;
  return p.pdf_position?.pageNumber ?? null;
}

export function blockQuote(b) {
  return b?.properties?.quote || "";
}

export function blockCollapsed(b) {
  return b?.properties?.collapsed ?? false;
}

// The block's place on its page's document under `key` (positionKeyOf: the
// PDF page's pdf_position, a picture's image_position, a text file's
// text_position; any other value, a map's index, reads as the PDF's), when
// it has one: a position with its rectangles (not one of its page alone),
// or a text range with a span (textSpan).
export function blockPosition(b, key = "pdf_position") {
  if (key === POSITION_KEY.text) {
    const pos = b?.properties?.text_position;
    return textSpan(pos) ? pos : null;
  }
  const pos = b?.properties?.[key === POSITION_KEY.image ? key : POSITION_KEY.pdf];
  return pos?.boundingRect ? pos : null;
}

// Where the block's own place is stored, by the block (positionKeyOf's twin):
// a region's image_position, a text range's text_position, else
// pdf_position.
export const placeKeyOf = (b) => (isRegionBlock(b) ? POSITION_KEY.image
  : isTextRangeBlock(b) && !isHighlightBlock(b) ? POSITION_KEY.text : POSITION_KEY.pdf);

// A place to check after the PDF was replaced, drawn dashed: approx, and
// not lost (a lost highlight is drawn hollow whatever else it carries).
export function placedToCheck(pos) {
  return Boolean(pos?.approx) && !pos.lost;
}

// A text range measured on another version than `fileHash`, the file shown,
// which the server's run has not placed on it yet: not lost, its `hash`
// another (one stored empty reads as the file's own). The server's rule
// (gamma/feedback/entries.py _placed, "pending": whether to check or not);
// false without a file to compare with.
export function rangePending(pos, fileHash) {
  return !!fileHash && isRecord(pos) && !pos.lost && typeof pos.hash === "string" && !!pos.hash && pos.hash !== fileHash;
}

// A passage matched roughly after its PDF was replaced: how alike its text
// and the text found for it are (the position's match), as a whole
// percentage; null for a place found exactly, set by hand or estimated.
export function roughMatchPercent(pos) {
  const m = placedToCheck(pos) ? pos.match : null;
  return typeof m === "number" && m >= 0 && m <= 1 ? Math.round(m * 100) : null;
}

// The position with its place accepted (Accept, on the review card, in the
// highlight's menu, or the Document menu's Accept all): approx, match and
// prev go, the rectangles stay, and the highlight is drawn solid. doc_id
// goes too: accepting says the rectangles are right on the PDF the page
// shows, so they no longer name another file (an area nothing placed keeps
// its old file's until then), and a later run neither reads them from that
// file nor dashes them again. A position with none of approx, match and
// prev is returned as it is.
export function acceptedPosition(pos) {
  if (!pos || !["approx", "match", "prev"].some((key) => key in pos)) return pos;
  const { approx, match, prev, doc_id, ...rest } = pos;
  return rest;
}

// A place to review after the PDF was replaced: one to check (dashed,
// placedToCheck) or one whose text the new file does not have (hollow,
// lost). The review card's queue walks these (pdf/highlightReview.js
// reviewQueue).
export function placeToReview(pos) {
  return placedToCheck(pos) || Boolean(pos?.lost);
}

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const placedRect = (r) => isObject(r) && ["x1", "y1", "x2", "y2"].every((k) => isNum(r[k]));
// The longest quote and the most rectangles a position's prev and a lost
// one's closest match keep (gamma/highlights.py PREV_MAX_QUOTE,
// PREV_MAX_RECTS): a longer quote is kept as its first PREV_MAX_QUOTE
// characters, cut wherever they end, inside a word too.
export const PREV_MAX_QUOTE = 1000;
export const PREV_MAX_RECTS = 200;
// The longest quote of a text range, in code points, its prev's and its
// closest's alike (gamma/highlights.py TEXT_QUOTE_MAX; textposition.json
// quote_max).
export const TEXT_QUOTE_MAX = 10000;

// The best place a run found for a lost highlight's passage, which read too
// unlike it to be applied (pdf_position.closest: below the account's "Apply
// a rough match from", at least half alike; gamma/highlight_relocate.py),
// read defensively: {position: {pageNumber, width, height, boundingRect,
// rects}, the candidate's place in the page's current PDF in the stored
// shape; match, 0..1; quote, its text as that file spells it}, or null when
// the position keeps none, or one malformed.
export function closestPlace(pos) {
  const c = pos?.closest;
  if (!isRecord(c)) return null;
  const { pageNumber, width, height, match } = c;
  if (!Number.isInteger(pageNumber) || pageNumber < 1 || !(isNum(width) && width > 0) || !(isNum(height) && height > 0)) return null;
  if (!isNum(match) || match < 0 || match > 1 || !Array.isArray(c.rects)) return null;
  const rects = c.rects.filter(placedRect).slice(0, PREV_MAX_RECTS).map(({ x1, y1, x2, y2 }) => ({ x1, y1, x2, y2 }));
  if (!rects.length) return null;
  const b = placedRect(c.boundingRect) ? c.boundingRect : null;
  const boundingRect = b ? { x1: b.x1, y1: b.y1, x2: b.x2, y2: b.y2 } : {
    x1: Math.min(...rects.map((r) => r.x1)), y1: Math.min(...rects.map((r) => r.y1)),
    x2: Math.max(...rects.map((r) => r.x2)), y2: Math.max(...rects.map((r) => r.y2)),
  };
  const quote = typeof c.quote === "string" ? c.quote.slice(0, PREV_MAX_QUOTE) : "";
  return { position: { pageNumber, width, height, boundingRect, rects }, match, quote };
}

// A lost highlight placed at its closest match ("Use closest match" on its
// review card): {position, quote}, the candidate's place and its text, so
// lost, closest, doc_id, approx, match and prev go with the old position
// and it is drawn solid; null when the position keeps no closest match.
// A lost text range (textSpan, a closest that names the version it was
// found in) is placed there to check, as a run's rough match is: the
// closest's span and hash, its own prefix and suffix (the next run respells
// them), approx, the closest's match, and prev where it was, with `quote`,
// the block's quote before (a menu's "Use the closest match"; Accept then
// draws it solid). A range's quotes are kept whole, to TEXT_QUOTE_MAX: the
// next run matches it by them, and the closest's is its quote from then on.
export function closestPlacement(pos, quote = "") {
  const own = textSpan(pos);
  if (own) {
    const c = pos.closest, span = textSpan(c);
    if (!span || typeof c.hash !== "string" || !isNum(c.match) || c.match < 0 || c.match > 1) return null;
    return {
      position: { ...span, hash: c.hash, prefix: typeof pos.prefix === "string" ? pos.prefix : "",
        suffix: typeof pos.suffix === "string" ? pos.suffix : "", approx: true, match: c.match,
        prev: { hash: typeof pos.hash === "string" ? pos.hash : "", ...own, quote: String(quote || "").slice(0, TEXT_QUOTE_MAX) } },
      quote: typeof c.quote === "string" ? c.quote.slice(0, TEXT_QUOTE_MAX) : "",
    };
  }
  const c = closestPlace(pos);
  return c ? { position: c.position, quote: c.quote } : null;
}

// Deep equality of two JSON values, whatever their keys' order.
export function sameJson(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => sameJson(v, b[i]));
  }
  if (!isObject(a) || !isObject(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => k in b && sameJson(a[k], b[k]));
}

// A highlight's properties with a review step taken back (the step's
// button on the review card pressed again: Accepted, Placed, Moved): `step` is
// {before: {position, quote?}, after}, the position before the step and
// the one it wrote; a quote in `before` is the step's to restore too (""
// drops it). The same object when the position is no longer the one the
// step wrote (changed since, by an undo or someone else), so nothing is
// taken back over a later change.
export function reviewUndone(props, step) {
  const before = step?.before;
  if (!before?.position || !sameJson(props?.pdf_position, step.after)) return props;
  const out = { ...props, pdf_position: before.position };
  if ("quote" in before) {
    if (before.quote) out.quote = before.quote;
    else delete out.quote;
  }
  return out;
}

// --- tree operations ---

export function findBlock(blocks, id) {
  for (const b of blocks) {
    if (b.id === id) return b;
    const x = findBlock(b.children || [], id);
    if (x) return x;
  }
  return null;
}

// The tree with block `id` replaced by `fn(block)`: only the path from the
// root to that block is copied, every other subtree keeps its identity
// (a memoized row on it does not re-render), and the same array comes back
// when the id is not in the tree. `fn` gets the stored block itself: it
// returns a new object and must not change the one it was given.
export function updateBlockTree(blocks, id, fn) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    let next;
    if (b.id === id) {
      next = fn(b);
    } else {
      const kids = b.children || [];
      const updated = updateBlockTree(kids, id, fn);
      if (updated === kids) continue;
      next = { ...b, children: updated };
    }
    const out = list.slice();
    out[i] = next;
    return out;
  }
  return list;
}

// The tree without block `id` and the blocks under it. Only the path to it
// is copied, as updateBlockTree copies it, and the same array comes back
// when the id is not in the tree.
export function removeBlockTree(blocks, id) {
  return extractBlock(blocks, id)?.remaining ?? (blocks || []);
}

// Where block `id` stands in the tree: {parentId, index}, its parent's id
// (null for a block at the top) and its index among its siblings; null
// when it is not in the tree. With insertBlockAt, a removed block is put
// back where it was (a review card's Discarded pressed again).
export function blockPlace(blocks, id, parentId = null) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    if (list[i].id === id) return { parentId, index: i };
    const found = blockPlace(list[i].children, id, list[i].id);
    if (found) return found;
  }
  return null;
}

// The tree with `block` inserted under `parentId` (null: at the top) at
// `index` among its siblings (past the last: at the end); the same array
// when that parent is not in the tree. Only the path to the parent is
// copied, as updateBlockTree copies it.
export function insertBlockAt(blocks, parentId, index, block) {
  const list = blocks || [];
  if (parentId == null) {
    const at = Math.max(0, Math.min(list.length, index));
    return [...list.slice(0, at), block, ...list.slice(at)];
  }
  return updateBlockTree(list, parentId, (p) => ({ ...p, children: insertBlockAt(p.children || [], null, index, block) }));
}

// Every block of the tree in document order, each a copy with its depth
// and parentId added (folding does not hide a block from a lookup — for
// what is on screen, see visibleBlocks; for one block by id, findBlock,
// which hands back the stored block itself).
export function flattenBlocks(blocks) {
  return walkBlocks(blocks, null);
}

function walkBlocks(blocks, view, depth = 0, parentId = null) {
  const out = [];
  for (const b of blocks || []) {
    out.push({ ...b, depth, parentId });
    if (!view || !isFolded(b, view)) {
      out.push(...walkBlocks(b.children || [], view, depth + 1, b.id));
    }
  }
  return out;
}

// --- the viewer's own state, beside the tree ---
// `editingId`: the one block whose editor is open. `folds`: the folding of
// the blocks this viewer touched (id → true folded / false open); a block
// nobody touched here shows its stored `properties.collapsed`, the default
// every viewer opens the page with.
export const EMPTY_VIEW = Object.freeze({ editingId: null, folds: Object.freeze({}) });

export function isFolded(block, view) {
  const own = view?.folds?.[block.id];
  return own === undefined ? blockCollapsed(block) : own;
}

export function withEditing(view, id) {
  return (view || EMPTY_VIEW).editingId === id ? view || EMPTY_VIEW : { ...(view || EMPTY_VIEW), editingId: id };
}

// Close `id`'s editor — a blur from an editor that is no longer the open
// one (another just opened) must not close that one.
export function closeEditing(view, id) {
  return view?.editingId === id ? { ...view, editingId: null } : view || EMPTY_VIEW;
}

// The blocks shown in the outliner, folded subtrees skipped.
export function visibleBlocks(blocks, view) {
  return walkBlocks(blocks, view || EMPTY_VIEW);
}

// Whether two trees hold the same blocks in the same places: the same
// ids in the same order under the same parents, whatever their text or
// properties. A tree copies only the path of an edit, so a keystroke
// compares one path; the list's motion (shared/ui/ListMotion.jsx) moves
// rows only when this is false.
export function sameShape(a, b) {
  if (a === b) return true;
  const x = a || [], y = b || [];
  if (x.length !== y.length) return false;
  for (let i = 0; i < x.length; i++) {
    if (x[i] === y[i]) continue;
    if (x[i].id !== y[i].id) return false;
    if (x[i].children !== y[i].children && !sameShape(x[i].children, y[i].children)) return false;
  }
  return true;
}

// How many blocks are under `blocks`, counting up to `most` + 1 at most
// (whether a subtree is larger than `most`, without walking all of it).
export function countBlocks(blocks, most = Infinity) {
  let n = 0;
  const walk = (list) => {
    for (const b of list || []) {
      if (++n > most) return;
      walk(b.children);
      if (n > most) return;
    }
  };
  walk(blocks);
  return n;
}

export function getParentInfo(blocks, id, parent = null) {
  for (let i = 0; i < (blocks || []).length; i++) {
    const b = blocks[i];
    if (b.id === id) {
      return { parent, index: i, block: b, siblings: blocks };
    }
    const x = getParentInfo(b.children || [], id, b);
    if (x) return x;
  }
  return null;
}

// A block made the last child of the sibling above it; the first of its
// siblings stays where it is.
export function indentBlock(blocks, id) {
  const info = getParentInfo(blocks, id);
  if (!info) return blocks;
  const { siblings, index, block } = info;
  if (index <= 0) return blocks;
  return insertChild(removeBlockTree(blocks, id), siblings[index - 1].id, block, true);
}

// A block put right after its parent, among its parent's siblings; a block
// at the top stays where it is.
export function outdentBlock(blocks, id) {
  const info = getParentInfo(blocks, id);
  return info?.parent ? insertSibling(removeBlockTree(blocks, id), info.parent.id, info.block, true) : blocks;
}

export function setBlockText(blocks, id, text) {
  return updateBlockTree(blocks, id, (b) => ({ ...b, content: text }));
}

// Fold or unfold `id`'s children: the viewer's own folding, and the stored
// `properties.collapsed` with it (a document edit — the default the page
// opens with next time, for everyone).
export function toggleFold(blocks, view, id) {
  const block = findBlock(blocks, id);
  if (!block) return { blocks, view };
  const folded = !isFolded(block, view);
  return {
    blocks: updateBlockTree(blocks, id, (b) => ({ ...b, properties: { ...b.properties, collapsed: folded } })),
    view: { ...view, folds: { ...view.folds, [id]: folded } },
  };
}

// The ancestors of `targetId` (the block itself excluded), top-most first;
// [] for a root block, null for an unknown id.
function ancestorsOf(blocks, targetId) {
  const walk = (list, chain) => {
    for (const b of list || []) {
      if (b.id === targetId) return chain;
      const found = walk(b.children, [...chain, b]);
      if (found) return found;
    }
    return null;
  };
  return walk(blocks, []);
}

// Unfold whatever hides `targetId` — a view change only, nothing the
// document notices. The same view comes back when nothing hid it.
export function revealBlock(blocks, view, targetId) {
  const hiding = (ancestorsOf(blocks, targetId) || []).filter((b) => isFolded(b, view));
  if (!hiding.length) return view;
  const folds = { ...view.folds };
  for (const b of hiding) folds[b.id] = false;
  return { ...view, folds };
}

// True when a folded ancestor hides `targetId` — what revealBlock opens.
export function isFoldedAway(blocks, targetId, view) {
  return (ancestorsOf(blocks, targetId) || []).some((b) => isFolded(b, view));
}

// A new block, empty unless `content`, `properties` or `children` are
// given, stamped with the moment it was made (`t0`, wall-clock ms on the
// clock a stroke's t0 reads), so a recording can place it
// (docs/dev/recording.md).
export function makeNewBlock({ id = makeId(), content = "", properties = {}, children = [] } = {}) {
  return { id, children, content, properties: { t0: wallNow(), ...properties } };
}

// New sibling next to `id`, empty unless `content` — below by default
// (Enter), above with `above` (the row's "+" handle with Alt held,
// Notion-style), or its first child with `firstChild`. `keep`, when given,
// becomes `id`'s text in the same transition (the new-block key's split:
// one undo step, one op set). A caller that needs the id before the tree
// changes makes the block itself and passes it as `newBlock`.
export function addSiblingBlock(blocks, id, { above = false, content, keep, firstChild = false, newBlock = makeNewBlock({ content }) } = {}) {
  const kept = keep === undefined ? blocks : setBlockText(blocks, id, keep);
  return {
    blocks: firstChild ? insertChild(kept, id, newBlock) : insertSibling(kept, id, newBlock, !above),
    newId: newBlock.id
  };
}

// Append a fresh empty block at the end of the top level (clicking the
// space under the last block — works even when the page has no blocks yet).
export function addRootBlock(blocks) {
  const newBlock = makeNewBlock();
  return {
    blocks: [...(blocks || []), newBlock],
    newId: newBlock.id
  };
}

// New empty last child of `id`.
export function addChildBlock(blocks, id) {
  const newBlock = makeNewBlock();
  return {
    blocks: insertChild(blocks, id, newBlock, true),
    newId: newBlock.id
  };
}

// --- highlights: a new highlight block, and the viewer's highlights ---

// The tree with a new highlight block last at the top level, made of what
// the viewer took from a selection: its `quote` (empty for an area or a
// region), `note` (the block's text), `position` (its place, written under
// `place`: pdf_position, or image_position for a picture's region), `color`
// in any spelling normalizeColor reads (a picker's, a swatch's), the
// default when it reads none, and `style` (markStyle.js: a line is stored,
// the wash, an area and a region store none).
export function addHighlightAsBlock(blocks, { id, quote = "", note = "", position, color, style, place = "pdf_position" }) {
  const block = makeNewBlock({
    id: id || makeId(),
    content: note || "",
    properties: withStyle({ color: normalizeColor(color) || DEFAULT_HIGHLIGHT, quote: quote || "", [place]: position }, style),
  });
  return [...(blocks || []), block];
}

// The viewer's highlights (PdfViewer's `highlights`, the image view's
// regions), one for each block placed under `key` (positionKeyOf) with a
// position, in document order: {id, quote, note, hasNote, color, style,
// position}, `quote` blockQuote's, `note` the block's own text, `color`
// blockColor's (stored #rrggbbaa, or the default), `style` markStyle.js
// blockStyle's and `position` blockPosition's. A region's position carries
// `area: true` for the readers that branch on it (the stored shape does
// not) and `onVersion`, whether it was drawn on `hash`, the picture the page
// shows now (its stored `hash`; false when it was drawn on another version,
// which the viewer draws dashed). A text range's is its own: whether it is
// pending is rangePending's, against the text the view shows.
export function blocksToHighlights(blocks, key = "pdf_position", hash = "") {
  const out = [];
  const region = key === POSITION_KEY.image, text = key === POSITION_KEY.text;
  const placed = region ? isRegionBlock : text ? isTextRangeBlock : isHighlightBlock;
  const shown = (pos) => (region ? { ...pos, area: true, onVersion: !!hash && pos.hash === hash } : pos);
  // A highlight "has a note" when the user typed anything on it: the block's
  // own content (the comment) or any non-empty block nested under it.
  function hasChildNote(b) {
    return (b.children || []).some((c) => (c.content || "").trim() !== "" || hasChildNote(c));
  }
  function walk(list) {
    for (const b of list || []) {
      const pos = placed(b) ? blockPosition(b, key) : null;
      if (pos) {
        out.push({
          id: b.id,
          quote: blockQuote(b),
          note: b.content || "",
          hasNote: (b.content || "").trim() !== "" || hasChildNote(b),
          color: blockColor(b),
          style: blockStyle(b),
          position: shown(pos),
        });
      }
      if (b.children?.length) walk(b.children);
    }
  }
  walk(blocks);
  return out;
}

// --- subtrees: containment, context, extract and insert ---

// True if `descendantId` is inside the subtree rooted at `ancestorId`
export function isDescendant(blocks, ancestorId, descendantId) {
  if (ancestorId === descendantId) return true;
  for (const b of blocks || []) {
    if (b.id === ancestorId) {
      return containsId(b.children || [], descendantId);
    }
    const hit = isDescendant(b.children || [], ancestorId, descendantId);
    if (hit) return true;
  }
  return false;
}

function containsId(blocks, id) {
  for (const b of blocks || []) {
    if (b.id === id) return true;
    if (containsId(b.children || [], id)) return true;
  }
  return false;
}

// Find a block by id along with its parent chain and position
// Returns { block, parentId, index, depth, ancestors: [id, ...] } or null
export function findBlockContext(blocks, id, depth = 0, ancestors = []) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === id) {
      return {
        block: b,
        parentId: ancestors[ancestors.length - 1] ?? null,
        index: i,
        depth,
        ancestors,
      };
    }
    const found = findBlockContext(b.children || [], id, depth + 1, [...ancestors, b.id]);
    if (found) return found;
  }
  return null;
}

// Extract a block (with subtree) from the tree, returning both the extracted block and the remaining tree.
export function extractBlock(blocks, id) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === id) {
      const extracted = b;
      const remaining = [...list.slice(0, i), ...list.slice(i + 1)];
      return { extracted, remaining };
    }
    const sub = extractBlock(b.children || [], id);
    if (sub) {
      const newChildren = sub.remaining;
      const newBlock = { ...b, children: newChildren };
      const newList = [...list.slice(0, i), newBlock, ...list.slice(i + 1)];
      return { extracted: sub.extracted, remaining: newList };
    }
  }
  return null;
}

// Insert `newBlock` as a sibling of `siblingId` (after=true means after, else before)
export function insertSibling(blocks, siblingId, newBlock, after) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === siblingId) {
      const insertAt = after ? i + 1 : i;
      return [...list.slice(0, insertAt), newBlock, ...list.slice(insertAt)];
    }
    const kids = b.children || [];
    const childResult = insertSibling(kids, siblingId, newBlock, after);
    if (childResult !== kids) {
      return [...list.slice(0, i), { ...b, children: childResult }, ...list.slice(i + 1)];
    }
  }
  return list;
}

// Insert `newBlock` as a child of `parentId` (atEnd=true for last child, else first)
export function insertChild(blocks, parentId, newBlock, atEnd = false) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === parentId) {
      const existing = b.children || [];
      const newChildren = atEnd ? [...existing, newBlock] : [newBlock, ...existing];
      return [...list.slice(0, i), { ...b, children: newChildren }, ...list.slice(i + 1)];
    }
    const kids = b.children || [];
    const childResult = insertChild(kids, parentId, newBlock, atEnd);
    if (childResult !== kids) {
      return [...list.slice(0, i), { ...b, children: childResult }, ...list.slice(i + 1)];
    }
  }
  return list;
}

// --- Keyboard block commands (editor/blockCommands.js) ---------------------

// Move a block one step among its siblings (dir -1 up, +1 down); the
// tree comes back unchanged (same reference) at the edge of its parent.
export function moveSibling(blocks, id, dir) {
  const list = blocks || [];
  const i = list.findIndex((b) => b.id === id);
  if (i >= 0) {
    const j = i + dir;
    if (j < 0 || j >= list.length) return list;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  }
  for (let k = 0; k < list.length; k++) {
    const b = list[k];
    if (!b.children?.length) continue;
    const children = moveSibling(b.children, id, dir);
    if (children !== b.children) {
      return [...list.slice(0, k), { ...b, children }, ...list.slice(k + 1)];
    }
  }
  return list;
}

// Remove a block, its children taking its place among the siblings (what
// deleting a line does to the indented lines under it: nothing).
export function removeBlockKeepChildren(blocks, id) {
  const list = blocks || [];
  const i = list.findIndex((b) => b.id === id);
  if (i >= 0) return [...list.slice(0, i), ...(list[i].children || []), ...list.slice(i + 1)];
  return list.map((b) => {
    if (!b.children?.length) return b;
    const children = removeBlockKeepChildren(b.children, id);
    return children === b.children ? b : { ...b, children };
  });
}

// The block shown right above (dir -1) or below (+1) `id` in the outliner,
// folded subtrees skipped, or null at either end.
export function visibleNeighbor(blocks, id, dir, view) {
  const flat = visibleBlocks(blocks, view);
  const i = flat.findIndex((b) => b.id === id);
  if (i < 0) return null;
  return flat[i + dir] || null;
}

// Backspace at the very start of plain note `id`: the block joins the one
// shown above it, or, when that one is empty, takes its place. Text only
// ever joins text: null when either block is no plain note, when the block
// above hides children (or a join would hide the ones it is given), or when
// the join would run into a fence, display math, a table, a divider, or this
// block's quote. Its children keep their reading order: spliced in at its
// place under a parent it merges into, otherwise last under the block above,
// which they already follow. {blocks, gone (the block removed), focusId,
// caret (where the caret goes in it)}; a thread on `gone` is the caller's to
// test, the threads being no part of the tree.
export function mergeIntoAbove(blocks, id, view) {
  const block = findBlock(blocks, id);
  const above = visibleNeighbor(blocks, id, -1, view);
  if (!block || !above || !isPlainNote(block) || !isPlainNote(above)) return null;
  const kids = block.children || [];
  const folded = isFolded(above, view);
  if (folded && above.children?.length) return null;
  const text = block.content || "", aboveText = above.content || "";
  if (!aboveText.trim()) return { blocks: removeBlockKeepChildren(blocks, above.id), gone: above.id, focusId: id, caret: 0 };
  if (folded && kids.length) return null;
  const first = text.split("\n", 1)[0], end = aboveText.length;
  if (isWholeLine(aboveText.slice(aboveText.lastIndexOf("\n") + 1)) || fenceInnerAt(aboveText, end) || findMathAtCursor(aboveText, end)?.display) return null;
  if (isWholeLine(first) || parseLine(first).depth) return null;
  const merged = aboveText + withoutFirstMarker(text);
  const next = above.id === getParentInfo(blocks, id).parent?.id
    ? setBlockText(removeBlockKeepChildren(blocks, id), above.id, merged)
    : updateBlockTree(removeBlockTree(blocks, id), above.id, (b) => ({ ...b, content: merged, children: [...(b.children || []), ...kids] }));
  return { blocks: next, gone: id, focusId: above.id, caret: aboveText.length };
}

// --- Several blocks at once (a selection of notes, editor/blockSelection.js) ---
// `ids` are the selection's roots in document order (selectionRoots): none
// of them is under another.

// The tree without the blocks `ids` and everything under them. Only the
// paths to them are copied; the same array comes back when none is there.
export function removeBlocks(blocks, ids) {
  const drop = new Set(ids);
  const walk = (list) => {
    let out = null;
    list.forEach((b, i) => {
      if (drop.has(b.id)) {
        out ??= list.slice(0, i);
        return;
      }
      const kids = b.children?.length ? walk(b.children) : b.children;
      const next = kids === b.children ? b : { ...b, children: kids };
      if (out) out.push(next);
      else if (next !== b) out = [...list.slice(0, i), next];
    });
    return out || list;
  };
  return walk(blocks || []);
}

// Each block made the last child of the sibling above it, as Tab does one
// (a run of siblings goes under the same one, in order); nothing moves when
// one of them is the first of its siblings and has none to go under.
export function indentBlocks(blocks, ids) {
  if (ids.some((id) => !(getParentInfo(blocks, id)?.index > 0))) return blocks;
  return ids.reduce((tree, id) => indentBlock(tree, id), blocks);
}

// Each block put right after its parent, as Shift+Tab does one; the last
// goes first, so a run of siblings keeps its order. A block at the top
// stays where it is.
export function outdentBlocks(blocks, ids) {
  return [...ids].reverse().reduce((tree, id) => outdentBlock(tree, id), blocks);
}

// A run of siblings one step up (dir -1) or down (+1) among the others, as
// Move block up / down does one; the same tree when the blocks are no one
// run under one parent, or the run is at that end.
export function moveSiblings(blocks, ids, dir) {
  const info = getParentInfo(blocks, ids[0]);
  if (!info) return blocks;
  const { parent, siblings, index } = info;
  const end = index + ids.length - 1;
  if (ids.some((id, i) => siblings[index + i]?.id !== id)) return blocks;
  const swap = dir < 0 ? index - 1 : end + 1;
  if (swap < 0 || swap >= siblings.length) return blocks;
  const next = siblings.slice();
  const [other] = next.splice(swap, 1);
  next.splice(dir < 0 ? end : index, 0, other);
  return parent ? updateBlockTree(blocks, parent.id, (p) => ({ ...p, children: next })) : next;
}

// The blocks `list` put into the tree one after another: the first where
// `at` says ({type: "child", id}: first under it; {type: "sibling", id,
// above}), each next one right after the one before.
export function insertBlocks(blocks, at, list) {
  let tree = blocks, prev = null;
  for (const b of list) {
    tree = prev ? insertSibling(tree, prev, b, true)
      : at.type === "child" ? insertChild(tree, at.id, b, false) : insertSibling(tree, at.id, b, !at.above);
    prev = b.id;
  }
  return tree;
}

// A copy of block `b` and everything under it with fresh ids, as Duplicate
// makes it. The anchoring stays with the original: a copy with the same
// position would draw the highlight, a picture's region or a text range
// twice (linkHighlightToBlock's rule), and so would the Zotero annotation an
// import made it from. The quote is kept.
export function freshCopy(b) {
  const { pdf_position, image_position, text_position, imported_annot, annot_stripped, zotero_annotation, ...props } = b.properties || {};
  return { ...b, id: makeId(), properties: props, children: (b.children || []).map(freshCopy) };
}

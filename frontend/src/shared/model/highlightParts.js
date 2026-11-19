// A highlight in parts (docs/dev/api.md "The highlight shape"): one
// highlight, with one note, over more than one passage of its page: a
// sentence a column break cuts, or two places one note is about. Nothing
// new is stored. A highlight's rectangles may lie anywhere on its page,
// and its quote is its parts' text in the order the page reads them, one
// space between two, as the server joins the stretches a highlight's
// rectangles cover (gamma/highlight_relocate.py _joined). A replaced PDF
// finds the parts again one piece after another
// (docs/dev/highlight_relocation.md), and the annotated PDF, Zotero and the
// readers take the rectangles as they take any highlight's.
import { blockQuote, extractBlock, findBlock, insertChild, isDescendant, isHighlightBlock, updateBlockTree } from "./blockModel.js";
import { applyPatch } from "./blockOps.js";

const finite = (r) => Boolean(r) && [r.x1, r.y1, r.x2, r.y2].every(Number.isFinite);

function union(rects) {
  return {
    x1: Math.min(...rects.map((r) => r.x1)), y1: Math.min(...rects.map((r) => r.y1)),
    x2: Math.max(...rects.map((r) => r.x2)), y2: Math.max(...rects.map((r) => r.y2)),
  };
}

// Whether a highlight's position can take another passage, or be one: a
// passage's (no area's) on a PDF page, with its rectangles, measured on
// the page's PDF as it is now: not one to review after the PDF was
// replaced (lost, dashed, or measured on another file), whose rectangles
// may be another file's.
export function takesParts(pos) {
  return Boolean(pos && Number.isInteger(pos.pageNumber) && Array.isArray(pos.rects) && pos.rects.some(finite)
    && !pos.area && !pos.lost && !pos.approx && !pos.doc_id);
}

// `pos`, whose text is `quote`, with the passage `part` added: a position
// on the same page in a frame of its own (a selection's, at the zoom it
// was made at), whose text is `partQuote`. Its rectangles are taken into
// pos's frame, after pos's own or, when it reads `before` them, ahead of
// them, so they stay in reading order; the bounding rect goes round both;
// the text is joined in the same order, with one space. {position, quote},
// or null when the two are not passages of one page (takesParts).
export function withPart(pos, quote, part, partQuote, { before = false } = {}) {
  if (!takesParts(pos) || !takesParts(part) || pos.pageNumber !== part.pageNumber) return null;
  const kx = part.width && pos.width ? pos.width / part.width : 1;
  const ky = part.height && pos.height ? pos.height / part.height : 1;
  const added = part.rects.filter(finite).map((r) => ({ x1: r.x1 * kx, y1: r.y1 * ky, x2: r.x2 * kx, y2: r.y2 * ky }));
  const own = pos.rects.filter(finite);
  const rects = before ? [...added, ...own] : [...own, ...added];
  const texts = (before ? [partQuote, quote] : [quote, partQuote]).map((s) => (s || "").trim()).filter(Boolean);
  return { position: { ...pos, boundingRect: union(rects), rects }, quote: texts.join(" ") };
}

// What makes a block a highlight (or a link region) rather than a note:
// its place, text and look, where it came from, and what it links to.
const HIGHLIGHT_KEYS = ["pdf_position", "quote", "color", "style", "imported_annot", "annot_stripped", "zotero_annotation",
  "link_url", "link_page_id", "link_block_id"];
const LINK_KEYS = ["link_url", "link_page_id", "link_block_id"];

// Whether highlight `fromId` can join highlight `intoId` (joinHighlights):
// two highlights that take parts, on one page, `from` no link region and
// `into` not under it.
export function canJoin(blocks, intoId, fromId) {
  const into = findBlock(blocks || [], intoId), from = findBlock(blocks || [], fromId);
  if (!into || !from || intoId === fromId || !isHighlightBlock(into) || !isHighlightBlock(from)) return false;
  if (LINK_KEYS.some((k) => from.properties[k])) return false;
  const a = into.properties.pdf_position, b = from.properties.pdf_position;
  return takesParts(a) && takesParts(b) && a.pageNumber === b.pageNumber && !isDescendant(blocks, fromId, intoId);
}

// The tree with highlight `fromId` joined to highlight `intoId`, as a part
// of it (withPart; `before`: from reads before into on the page). Into
// keeps its id, look, note and place in the notes; what was written on
// from stays, under it: a from with a note of its own (its text) or a
// comment thread (`keep`, which the server knows of, not the tree) goes
// there whole as a plain note, keeping its id, which its threads hang on,
// and its children; one with neither gives its children to into. null when
// the two cannot join (canJoin).
export function joinHighlights(blocks, intoId, fromId, { before = false, keep = false } = {}) {
  if (!canJoin(blocks, intoId, fromId)) return null;
  const into = findBlock(blocks, intoId), from = findBlock(blocks, fromId);
  const joined = withPart(into.properties.pdf_position, blockQuote(into), from.properties.pdf_position, blockQuote(from), { before });
  let tree = extractBlock(blocks, fromId).remaining;
  tree = updateBlockTree(tree, intoId, (b) => ({
    ...b, properties: applyPatch(b.properties, { pdf_position: joined.position, quote: joined.quote || null }),
  }));
  const note = { ...from, properties: applyPatch(from.properties, Object.fromEntries(HIGHLIGHT_KEYS.map((k) => [k, null]))) };
  const moved = (from.content || "").trim() || keep ? [note] : from.children || [];
  for (const b of moved) tree = insertChild(tree, intoId, b, true);
  return tree;
}

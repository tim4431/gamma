// The reference list on a page of the PDF translated view, read from its
// segmented blocks by their shape alone, since most lists have no heading of
// their own and a hanging indent cuts an entry into two blocks. Pure, like
// pdf/pageFlow.js, and per page: a block's answer depends on no order of
// work. docs/dev/translation.md "The reference list" describes the rule.
//
// `pageList` marks no block, since blocks are cached and shared and the page's
// layout reads them. It gives a summary the planner keeps with the page's flow:
// `{blocks: Set of indices, alone}`. `blocks` are the chains of cites and what
// reads as their entries' pieces. `alone`: there are enough cites for a list
// without help. Otherwise the blocks count only beside a page that is `alone`
// (`inList`), and a page beside none translates as it would without the rule.
import { isCaption } from "./pageFlow.js";

// Chained cites that make a list by themselves. Four would take a citation
// table and body text; a neighbouring list takes any chain.
const ALONE_CITES = 6;
// A chain has two cites or more, none further from the last than this many
// times their size.
const CHAIN_CITES = 2;
const CHAIN_GAP = 8;
// Lower-case words per year a cite has at most, and the lower-case words that
// make a block a paragraph rather than a piece of an entry.
const CITE_WORDS = 16;
const PROSE_WORDS = 20;
// The space, in the block's size, across which a block touches a chain.
const TOUCHES = 1.2;

const YEARS = /(?:19|20)\d{2}[a-z]?\b/g;
// "96, 010401 (2006)", "572, 368–372 (2019)", "A 69, 021403(R) (2004)", "12(3), 45–60 (1998)"
const VOLUME_PAGE_YEAR = /\b\d{1,5}[A-Z]?(?:\(\d+\))?\s*[,:]\s*[A-Za-z]?\d{1,8}(?:\s*[–—-]\s*[A-Za-z]?\d+)?(?:\s*\([A-Z]\))?\s*\(\s*(?:[A-Z][a-z]+\.?\s+)?(?:19|20)\d{2}[a-z]?\s*\)/;
// "12 (2003) 1–20"
const VOLUME_YEAR_PAGE = /\b\d{1,5}\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)\s*[,:]?\s*(?:pp?\.?\s*)?[A-Za-z]?\d{1,8}/;
const PAGES = /\bpp?\.\s*\d+/;
const IDENTIFIER = /arXiv[: ]|\bdoi\b|10\.\d{4,5}\/|Preprint at|https?:\/\/arxiv/i;
const OPENER = /^\s*(?:\[\d{1,4}\]|\(\d{1,4}\)|\d{1,4}\.\s|\d{1,4}\)\s)/;
const INITIAL_NAME = /\b\p{Lu}\.(?:[\s-]?\p{Lu}\.)*\s?(?:(?:van|von|de|der|den|di|du|la|le)\s)*\p{Lu}[\p{L}’'-]+/gu; // A. B. Smith
const NAME_INITIAL = /\b\p{Lu}[\p{L}’'-]+,\s\p{Lu}\.(?:[\s-]?\p{Lu}\.)*/gu; // Smith, A. B.
// An entry's tail: ", 2019." or "(2019)" at the block's end, not a date.
const COMMA_YEAR = /(?:,\s*|\(\s*)((?:19|20)\d{2}[a-z]?)\)?[.;]?\s*$/;
const DATE = /(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{0,2},?\s*$/;
// What ends a list, or stands above one, rather than belongs to it.
const LABEL = /^\s*(?:acknowledge?ments?|author\s+contributions?|author\s+information|competing\s+interests?|conflicts?\s+of\s+interest|funding|data\s+availability|code\s+availability|additional\s+information|supplementary|publisher|open\s+access|reprints|correspondence|peer\s+review|online\s+content|appendix)\b/i;
const LIST_HEADING = /^(?:references|bibliography)\.?$/i;

const count = (text, words) => (text.match(words) || []).length;
const lowerWords = (text) => count(text, /(?:^|\s)\p{Ll}{3,}(?=[\s,.;:]|$)/gu);

// Whether `text` ends on a year that closes an entry.
function yearTail(text) {
  const m = COMMA_YEAR.exec(text);
  return !!m && !DATE.test(text.slice(0, m.index + 1).replace(/,\s*$/, "").slice(-16));
}

// A block as the rule reads it, `i` its index on the page. `cite`: it holds a
// reference: a volume, page and year; an identifier; a year with a name, an
// opening number or a page; or an entry's tail, and few words for its years.
function read(p, i) {
  const text = p.text.trim();
  const years = count(text, YEARS);
  const names = count(text, INITIAL_NAME) + count(text, NAME_INITIAL);
  const opener = OPENER.test(text);
  const words = lowerWords(text);
  const entry = VOLUME_PAGE_YEAR.test(text) || VOLUME_YEAR_PAGE.test(text)
    || (years > 0 && (names >= 1 || opener || PAGES.test(text))) || yearTail(text);
  const label = LABEL.test(text);
  return {
    i, x1: p.x1, x2: p.x2, y1: p.y1, y2: p.y2, size: p.size,
    // A piece of an entry, and one that can open an entry or go on from one.
    piece: !label && !LIST_HEADING.test(text) && words < PROSE_WORDS,
    opens: names >= 1 || opener || /^\p{Ll}/u.test(text),
    cite: !label && !isCaption(p) && (entry || IDENTIFIER.test(text)) && words <= CITE_WORDS * Math.max(1, years),
    // Shaped as an entry, not only by an identifier as a data statement is.
    entry,
    long: text.length > 4,
  };
}

const similar = (a, b) => Math.abs(a.size - b.size) <= Math.max(a.size, b.size) * 0.1;
const sameColumn = (a, b) => Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1) > 0.5 * Math.min(a.x2 - a.x1, b.x2 - b.x1);
const byTop = (a, b) => a.y1 - b.y1;

// The cites of the page's commonest size, as columns, as chains: a chain of
// one is a running head or a stray citation. Distances are in the cites' own
// size, not the page's body size, which is read with the list left out.
function chainsOf(blocks) {
  const cites = blocks.filter((b) => b.cite);
  let main = [];
  for (const c of cites) {
    const same = cites.filter((o) => similar(o, c));
    if (same.length > main.length) main = same;
  }
  const columns = [];
  for (const c of [...main].sort((a, b) => a.x1 - b.x1)) {
    let col = columns.find((k) => sameColumn(k, c));
    if (!col) columns.push((col = { x1: c.x1, x2: c.x2, cites: [] }));
    col.x1 = Math.min(col.x1, c.x1);
    col.x2 = Math.max(col.x2, c.x2);
    col.cites.push(c);
  }
  const chains = [];
  for (const col of columns) {
    const items = col.cites.sort(byTop);
    let chain = [], bottom = 0;
    for (const c of items) {
      if (chain.length && c.y1 - bottom > CHAIN_GAP * items[0].size) {
        chains.push({ col, cites: chain });
        chain = [];
      }
      bottom = chain.length ? Math.max(bottom, c.y2) : c.y2;
      chain.push(c);
    }
    chains.push({ col, cites: chain });
  }
  return chains.filter((c) => c.cites.length >= CHAIN_CITES);
}

// A chain's blocks: all of its column between its first cite and its last at
// its size, and the blocks that touch those ends and read as pieces of an
// entry, up to a section label or a paragraph.
function chainBlocks({ col, cites }, blocks) {
  const column = blocks.filter((b) => sameColumn(col, b) && similar(b, cites[0])).sort(byTop);
  let top = Math.min(...cites.map((c) => c.y1)), bottom = Math.max(...cites.map((c) => c.y2));
  const taken = column.filter((b) => b.y1 >= top - 1 && b.y2 <= bottom + 1);
  const middle = (b) => (b.y1 + b.y2) / 2;
  for (const b of column) {
    if (middle(b) < bottom) continue;
    if (b.y1 - bottom > b.size * TOUCHES || !b.piece) break;
    taken.push(b);
    bottom = Math.max(bottom, b.y2);
  }
  for (const b of [...column].reverse()) {
    if (middle(b) > top) continue;
    if (top - b.y2 > b.size * TOUCHES || !b.piece || !b.opens) break;
    taken.push(b);
    top = Math.min(top, b.y1);
  }
  return taken.filter((b) => b.cite || b.piece);
}

// The page's summary. Each page's own blocks decide it, the blocks that do
// not read as text included, in whatever size the list is set. A chain that would count only
// through a neighbour needs a cite shaped as an entry: two blocks with a doi
// each are data statements after a list as often as its end.
export function pageList(paras) {
  const blocks = paras.map(read).filter((b) => b.long);
  const chains = chainsOf(blocks);
  const alone = chains.reduce((n, c) => n + c.cites.length, 0) >= ALONE_CITES;
  const listed = new Set();
  for (const chain of chains) {
    if (!alone && !chain.cites.some((c) => c.entry)) continue;
    for (const b of chainBlocks(chain, blocks)) listed.add(b.i);
  }
  return { blocks: listed, alone };
}

// Whether block `i` is in the list of a page whose summary is `list`, beside
// pages with the summaries `before` and `after` (null: not segmented, which
// counts as no list, so that a page alone decides by itself).
export function inList(i, list, before, after) {
  return list.blocks.has(i) && (list.alone || !!before?.alone || !!after?.alone);
}

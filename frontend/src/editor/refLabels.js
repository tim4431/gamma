// The labels a row's [[ref]] chips and Gamma-link cards show, resolved for
// every row of the page at once (App, once per tree change) instead of by
// each row from the flattened tree: a memoized row (BlockTree.jsx) takes its
// own entry, which keeps its identity while its labels read the same, so a
// keystroke in one block re-renders no other row.
import { gammaLinkIds } from "../shared/model/gammaLinks.js";
import { sameObject } from "../shared/lib/keepIfSame.js";

export const NO_LABELS = Object.freeze({});

// A [[ref]] in a block's content, its id as the group: the one spelling
// every scan of a block's refs uses (the rows, the backlinks, the picker).
export const REF_RE = /\[\[([a-zA-Z0-9_-]+)\]\]/g;

// A cached ref as the chips and embed cards read it: the block's text and
// its page's title, or, for a block no page holds (App's onFetchRefs), its
// page's Recently deleted entry (`trashed`) or `missing`.
export function refLabelOf(rb) {
  if (rb.trashed) return { trashed: rb.trashed };
  if (rb.missing) return { missing: true };
  return { content: rb.content, page_title: rb.page_title };
}

// The ids a block's content names, parsed once per block object (the tree's
// nodes keep their identity until they change): `[id, ref]`, ref true for a
// [[ref]] (shown even when its block is trashed or missing), false for a
// Gamma link (an ordinary URL when it does not resolve).
const REF_IDS = new WeakMap();
export function refIdsOf(block) {
  let ids = REF_IDS.get(block);
  if (ids) return ids;
  const content = block.content || "";
  ids = [...content.matchAll(REF_RE)].map((m) => [m[1], true]);
  for (const id of gammaLinkIds(content)) if (!ids.some(([x]) => x === id)) ids.push([id, false]);
  REF_IDS.set(block, ids);
  return ids;
}

function sameLabel(a, b) {
  return a === b || (!!a && !!b && a.content === b.content && a.page_title === b.page_title
    && a.trashed === b.trashed && a.missing === b.missing);
}

const sameLabels = (a, b) => sameObject(a, b, sameLabel);

// Map block id → {refId: label} for every block of `tree` whose content
// names a ref: `byId` resolves a page's own blocks, `refCache` the ones
// fetched from elsewhere. An entry equal to the one in `prev` is the same
// object.
export function refLabelsByBlock(tree, byId, refCache, prev) {
  const next = new Map();
  const walk = (list) => {
    for (const block of list || []) {
      const ids = refIdsOf(block);
      if (ids.length) {
        const labels = {};
        for (const [id, ref] of ids) {
          if (labels[id]) continue;
          const rb = byId.get(id) || refCache?.[id];
          if (rb && (ref || !(rb.trashed || rb.missing))) labels[id] = refLabelOf(rb);
        }
        const old = prev?.get(block.id);
        next.set(block.id, old && sameLabels(old, labels) ? old : labels);
      }
      if (block.children?.length) walk(block.children);
    }
  };
  walk(tree);
  return next;
}

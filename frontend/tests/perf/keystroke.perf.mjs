// npm run test:perf (from frontend/) — a keystroke in a note on a page of
// 1,000 blocks: the pure work App does for each key, timed with its
// pieces, each held to a budget (docs/dev/debugging.md "Performance
// budgets"). What React renders is not timed here.
import assert from "node:assert/strict";
import { test } from "node:test";
import { blocksById, pageHighlights, recordingList, sheetNumbersOf, stampedList } from "../../src/app/treeDerived.js";
import { classifyTransition, createHistory, observeTree } from "../../src/editor/blockHistory.js";
import { refLabelsByBlock } from "../../src/editor/refLabels.js";
import { sheetsOf } from "../../src/notebook/notebook.js";
import { keepIfSame, keepIfSameJson, sameMap } from "../../src/shared/lib/keepIfSame.js";
import { blocksToHighlights, findBlock, flattenBlocks, updateBlockTree } from "../../src/shared/model/blockModel.js";
import { diffTrees } from "../../src/shared/model/blockOps.js";
import { PAGE_ID, annotatedPage } from "./fixtures.mjs";
import { budget, measure } from "./measure.mjs";

const KEYS = 200;
const { tree, pos, lastId, highlightIds } = annotatedPage();
// a few highlights with open comment threads (useComments' countByBlock)
const countByBlock = new Map(highlightIds.slice(0, 20).map((id, i) => [id, 1 + (i % 3)]));
const typed = (blocks) => updateBlockTree(blocks, lastId, (b) => ({ ...b, content: `${b.content}x` }));

// App's state that outlives a render: the refs its memos keep results in,
// the undo history (useBlockHistory) and the collab session's base.
function session() {
  const refs = { labels: { current: new Map() }, sheets: { current: new Map() },
    recording: { current: { json: "", value: [] } }, stamped: { current: { json: "", value: [] } },
    highlights: { current: { json: "", value: [] } } };
  const s = { blocks: tree, base: tree, history: createHistory(tree), refs };
  derive(s, tree);
  return s;
}

// What App.jsx computes from a new tree, in its order: the memos of the
// render (nbSheets, focusedNote, treeBlocks, treeById, refLabelsById,
// sheetNumbers, recordingBlocks, stampedBlocks, annotatedPage, highlights).
function derive(s, blocks) {
  const { refs } = s;
  const nbSheets = sheetsOf(blocks);
  const focused = findBlock(blocks, lastId);
  const treeBlocks = flattenBlocks(blocks);
  const treeById = blocksById(treeBlocks);
  const labels = refs.labels.current = refLabelsByBlock(blocks, treeById, {}, refs.labels.current);
  keepIfSame(refs.sheets, sheetNumbersOf(nbSheets), sameMap);
  keepIfSameJson(refs.recording, recordingList(blocks));
  keepIfSameJson(refs.stamped, stampedList(blocks));
  const annotated = blocksToHighlights(blocks).length >= 5;
  const highlights = keepIfSameJson(refs.highlights, pageHighlights(blocks, treeBlocks, countByBlock));
  return { focused, labels, annotated, highlights };
}

// `n` keystrokes at the end of the page's last note: the tree edit, the
// render's memos, then the effects, the history's observeTree and the
// collab session's diff against its base. `check(key)` sees each key.
function typeKeys(s, n, check) {
  for (let k = 0; k < n; k++) {
    const prev = s.blocks, next = typed(prev);
    const derived = derive(s, next);
    observeTree(s.history, next, {}, lastId);
    const ops = diffTrees(s.base, next, PAGE_ID, pos);
    s.blocks = s.base = next;
    check?.({ prev, next, derived, ops });
  }
}

test(`${KEYS} keystrokes at the last block of a 1,000-block page, everything App runs per key`, async (t) => {
  const s = session();
  const firstLabels = s.refs.labels.current, firstHighlights = s.refs.highlights.current.value;
  let typedSoFar = findBlock(tree, lastId).content;
  typeKeys(s, KEYS, ({ prev, next, derived, ops }) => {
    assert.deepEqual(ops, [{ op: "set", id: lastId, content: `${typedSoFar}x`, base: typedSoFar }]);
    assert.equal(classifyTransition(prev, next), lastId);
    assert.equal(derived.highlights, firstHighlights, "the highlights keep their identity");
    assert.equal(derived.highlights.length, 400);
    for (const [id, labels] of derived.labels) assert.equal(labels, firstLabels.get(id));
    typedSoFar += "x";
  });
  assert.ok(findBlock(s.blocks, lastId).content.endsWith("x".repeat(KEYS)));
  assert.equal(s.history.undo.length, 1, "one undo step for the run of typing");
  budget(t, await measure((s) => typeKeys(s, KEYS), { setup: session }), 1400);
});

test(`updateBlockTree x${KEYS}: one key at the last block`, async (t) => {
  const next = typed(tree);
  assert.equal(next.length, tree.length);
  assert.ok(next.slice(0, -1).every((b, i) => b === tree[i]), "every other subtree keeps its identity");
  assert.notEqual(next.at(-1), tree.at(-1));
  budget(t, await measure(() => { for (let k = 0; k < KEYS; k++) typed(tree); }), 7);
});

test(`diffTrees x${KEYS}: one key against the base`, async (t) => {
  const next = typed(tree);
  assert.equal(diffTrees(tree, next, PAGE_ID, pos).length, 1);
  budget(t, await measure(() => { for (let k = 0; k < KEYS; k++) diffTrees(tree, next, PAGE_ID, pos); }), 300);
});

test(`App's highlights x${KEYS}: blocksToHighlights over 1,000 blocks, kept by their JSON`, async (t) => {
  const next = typed(tree), flat = flattenBlocks(next);
  const ref = { current: { json: "", value: [] } };
  const first = keepIfSameJson(ref, pageHighlights(tree, flattenBlocks(tree), countByBlock));
  assert.equal(first.length, 400);
  assert.equal(first.filter((h) => h.threadCount).length, 20);
  assert.equal(keepIfSameJson(ref, pageHighlights(next, flat, countByBlock)), first);
  budget(t, await measure(() => { for (let k = 0; k < KEYS; k++) keepIfSameJson(ref, pageHighlights(next, flat, countByBlock)); }), 800);
});

test(`refLabelsByBlock x${KEYS}: 60 notes with [[refs]], kept while they read the same`, async (t) => {
  const next = typed(tree), byId = blocksById(flattenBlocks(next));
  const prev = refLabelsByBlock(tree, blocksById(flattenBlocks(tree)), {}, new Map());
  const again = refLabelsByBlock(next, byId, {}, prev);
  assert.equal(again.size, 60);
  assert.ok([...again].every(([id, labels]) => labels === prev.get(id) && Object.keys(labels).length === 2));
  budget(t, await measure(() => { for (let k = 0; k < KEYS; k++) refLabelsByBlock(next, byId, {}, prev); }), 20);
});

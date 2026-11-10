// npm run test:perf (from frontend/) — another client's batch applied to a
// page of 1,000 blocks (blockOps.js applyOps, what the page socket's ops
// go through), held to budgets (docs/dev/debugging.md "Performance
// budgets").
import assert from "node:assert/strict";
import { test } from "node:test";
import { generateKeyBetween } from "fractional-indexing";
import { findBlock } from "../../src/shared/model/blockModel.js";
import { applyOps } from "../../src/shared/model/blockOps.js";
import { PAGE_ID, annotatedPage } from "./fixtures.mjs";
import { budget, measure } from "./measure.mjs";

const { tree, pos, highlightIds } = annotatedPage();

test("applyOps x200: a peer's one-set batch", async (t) => {
  const target = highlightIds[200];
  const batch = [{ op: "set", id: target, content: "a peer typed this" }];
  const out = applyOps(tree, batch, PAGE_ID, pos);
  assert.equal(findBlock(out, target).content, "a peer typed this");
  const at = tree.findIndex((b) => b.id === target);
  assert.ok(out.every((b, i) => i === at || b === tree[i]), "every other subtree keeps its identity");
  budget(t, await measure(() => { for (let k = 0; k < 200; k++) applyOps(tree, batch, PAGE_ID, pos); }), 4);
});

test("applyOps x5: a peer's batch of 50 inserts among the page's top-level blocks", async (t) => {
  // spread over the page, each between two neighbours
  const batch = Array.from({ length: 50 }, (_, i) => {
    const at = i * 13;
    return { op: "insert", id: `peer${i}`, parent: PAGE_ID, position: generateKeyBetween(tree[at].position, tree[at + 1].position),
      content: `a peer's line ${i}`, props: {} };
  });
  const out = applyOps(tree, batch, PAGE_ID, new Map(pos));
  assert.equal(out.length, tree.length + 50);
  assert.ok(out.every((b, i) => i === 0 || out[i - 1].position < b.position), "the siblings stay in key order");
  assert.equal(out[out.findIndex((b) => b.id === "peer3") - 1].id, tree[39].id);
  budget(t, await measure(() => { for (let k = 0; k < 5; k++) applyOps(tree, batch, PAGE_ID, new Map(pos)); }), 50);
});

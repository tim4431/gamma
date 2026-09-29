import assert from "node:assert/strict";
import { test } from "node:test";
import { encodeStroke, decodeStroke, newNotebookInk, eraseAt, duplicateStrokes, translateStrokes, rebaseInkEdit } from "../src/ink/ink.js";
import { replayInk } from "../src/ink/inkReplay.js";
import { saveInk } from "../src/ink/inkSave.js";
import { diffTrees, pushOp } from "../src/shared/model/blockOps.js";

const stroke = encodeStroke({ id: "stroke", ch: "xypt", samples: Array.from({ length: 11 }, (_, i) => ({ x: i * 10, y: 20, p: 0.5, t: i * 100 })) });
const ink = { ...newNotebookInk("sheet", 612, 792), strokes: [stroke] };
const replay = (ms) => ({ segmentId: "seg", segmentIds: ["seg"], ms, events: [
  { kind: "stroke", segment_id: "seg", block_id: "group", stroke_id: "stroke", start_ms: 100, end_ms: 1100 },
] });
test("replay hides future ink and follows the surviving moved fragments at their original sample times", () => {
  assert.equal(replayInk(ink, "group", replay(50)).strokes.length, 0);
  assert.equal(decodeStroke(replayInk(ink, "group", replay(350)).strokes[0]).length, 3);
  const split = eraseAt(ink, 50, 20, 3).ink;
  assert.equal(split.strokes.length, 2);
  assert.equal(split.version, 2);
  assert(split.strokes.every((s) => s.source_id === "stroke"));
  const moved = translateStrokes(split, split.strokes.map((s) => s.id), 0, 10);
  const partial = replayInk(moved, "group", replay(650));
  assert.equal(partial.strokes.length, 1);
  assert.equal(decodeStroke(partial.strokes[0])[0].y, 30);
  assert.deepEqual(replayInk(moved, "group", replay(2000)), moved);
  const duplicate = duplicateStrokes(split, [split.strokes[0].id], 10, 10).ink.strokes.at(-1);
  assert.equal(duplicate.source_id, undefined);
});
test("native-origin notebook ink uses the ordinary guarded block save path", async () => {
  const calls = [];
  const props = await saveInk({ api: async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    return calls.length === 1 ? { url: "/api/uploads/saved.ink" } : {};
  }, id: "group", pageId: "page", ink, baseUrl: "/api/uploads/old.ink", batch: "stable-batch" });
  assert.equal(calls[1][0], "/api/pages/page/ops");
  assert.deepEqual(calls[1][1].ops[0].base_props, { ink_url: "/api/uploads/old.ink" });
  assert.equal(calls[1][1].batch, "stable-batch");
  assert.equal(props.sheet_id, "sheet");
  assert.equal(props.pdf_page, null);
});

test("queued block edits preserve the earliest ink precondition when coalescing", () => {
  const before = [{ id: "group", content: "", properties: { ink_url: "before" }, position: "a0", children: [] }];
  const after = [{ ...before[0], properties: { ink_url: "next" } }];
  const ops = diffTrees(before, after, "page", new Map([["group", "a0"]]));
  assert.deepEqual(ops[0].base_props, { ink_url: "before" });
  pushOp(ops, { op: "set", id: "group", props: { ink_url: "newest" }, base_props: { ink_url: "next" } });
  assert.equal(ops[0].props.ink_url, "newest");
  assert.equal(ops[0].base_props.ink_url, "before");
});

test("undo removes our stroke without removing remote additions and refuses changed strokes", () => {
  const empty = { ...ink, strokes: [] };
  const remote = { ...stroke, id: "remote" };
  const current = { ...ink, strokes: [stroke, remote] };
  assert.deepEqual(rebaseInkEdit(current, ink, empty).strokes, [remote]);
  assert.equal(rebaseInkEdit({ ...ink, strokes: [{ ...stroke, color: "#ff0000" }] }, ink, empty), null);
});

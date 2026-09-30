// node --test tests/replica.test.mjs (from frontend/) — the iPad replica's
// pure core (src/replica/*): the tree rules against the Python engine's
// cases (tests/shared/synctree.json), the local merges a replica makes
// without a server, the edit-beats-delete rule, the views. The rounds
// against a real server are the e2e group `replica`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { encodeStroke, newCanvasInk, newInk } from "../src/ink/ink.js";
import { firstSheetId, newSheet } from "../src/notebook/notebook.js";
import { ownEdits, reconcileRemoteOps, split, unlanded } from "../src/replica/reconcile.js";
import { apply, applyLocal, diff, moved, snapshotFromTree, treeOf, uploadRefs } from "../src/replica/tree.js";
import { libraryRows, pageView } from "../src/replica/views.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/synctree.json", import.meta.url), "utf8"));
const P = SHARED.page;

for (const c of SHARED.diff) {
  test(`tree diff: ${c.note}`, () => {
    assert.deepEqual(diff(c.base, c.target, P), c.ops);
    assert.deepEqual(apply(c.base, c.ops), c.applied);
    assert.deepEqual([...moved(c.base, c.target)].sort(), c.moved);
  });
}
for (const c of SHARED.unlanded) {
  test(`unlanded: ${c.note}`, () => assert.deepEqual(unlanded(c.op, c.base, c.remote), c.result));
}

const b = (parent, position, content = "", props = {}) => ({ parent, position, content, props });
const stroke = (id, x) => encodeStroke({ id, samples: [{ x, y: 10 }, { x: x + 5, y: 12 }] });

function inkStore() {
  const files = new Map();
  let n = 0;
  return {
    files,
    put(ink) { const url = `/api/uploads/${String(++n).padStart(24, "0")}.ink`; files.set(url, ink); return url; },
    read: async (url) => files.get(url) || null,
    store: async (ink) => { const url = `/api/uploads/${String(++n).padStart(24, "0")}.ink`; files.set(url, ink); return url; },
  };
}

test("applying another side's ops here merges text and drawings the way the server would", async () => {
  const ink = inkStore();
  const u0 = ink.put({ ...newInk(1, 612, 792), strokes: [stroke("a", 10)] });
  const uHere = ink.put({ ...newInk(1, 612, 792), strokes: [stroke("a", 10), stroke("b", 50)] });
  const uThere = ink.put({ ...newInk(1, 612, 792), strokes: [stroke("a", 10), stroke("x", 90)] });
  const here = { [P]: b("root", "a0", "Page"), n1: b(P, "a0", "hello world!"),
    g1: b(P, "a1", "", { ink_url: uHere, pdf_page: 1, ink_strokes: 2 }) };
  const ops = [
    { op: "set", id: "n1", content: "hello brave world", base: "hello world" },
    { op: "set", id: "g1", props: { ink_url: uThere, ink_strokes: 2, pdf_position: null }, base_props: { ink_url: u0 } },
    { op: "insert", id: "n1", parent: P, position: "a9", content: "a retry", props: {} },
    { op: "insert", id: "orphan", parent: "gone", position: "a0", content: "", props: {} },
  ];
  const { snapshot, applied, merged } = await applyLocal(here, ops, ink);
  assert.equal(snapshot.n1.content, "hello brave world!");
  assert.deepEqual(merged.map((m) => [m.id, m.result]), [["n1", "hello brave world!"]]);
  const drawn = await ink.read(snapshot.g1.props.ink_url);
  assert.deepEqual(drawn.strokes.map((s) => s.id), ["a", "b", "x"]);
  assert.equal(snapshot.g1.props.ink_strokes, 3);
  assert.equal(snapshot.g1.props.pdf_position.pageNumber, 1);
  assert.equal(snapshot.n1.position, "a0", "an insert of a known id changes nothing");
  assert.ok(!("orphan" in snapshot), "no parent, no block");
  assert.equal(applied.length, 2);
  assert.equal(here.n1.content, "hello world!", "the input is not changed");
});

test("an edit beats a delete, both ways, and only this copy's edits are pushed", () => {
  const base = { [P]: b("root", "a0", "Page"), a: b(P, "a0", "alpha"), s: b(P, "a1", "sub"), s1: b("s", "a0", "under") };
  const local = { ...base, a: b(P, "a0", "alpha, edited here") };
  delete local.s; delete local.s1;          // deleted here
  const remote = { ...base, s1: b("s", "a0", "under, edited there") };
  delete remote.a;                          // deleted there
  const conflicts = [];
  const ops = reconcileRemoteOps(base, local, remote, P, conflicts);
  assert.ok(!ops.some((op) => op.op === "delete" && op.id === "a"), "the remote's delete of what was edited here is dropped");
  assert.deepEqual(ops.filter((op) => op.op === "insert").map((op) => op.id), ["s", "s1"], "the subtree edited there comes back");
  assert.deepEqual(conflicts.map((c) => c.kind).sort(), ["kept_local_edit", "restored_remote_edit"]);
  const merged = apply(local, ops);
  const push = split(diff(remote, merged, P), ownEdits(base, local));
  assert.deepEqual(push.map((op) => [op.op, op.id]), [["insert", "a"]], "a goes back there; nothing else is pushed");
});

test("snapshots, trees and upload references", () => {
  const snap = snapshotFromTree({ id: P, parent_id: "root", position: "a0", content: "Page", properties: { doc_id: "d".repeat(24) },
    children: [{ id: "n1", parent_id: P, position: "a1", content: "see ![x](/api/uploads/abc123.png).", properties: {},
      children: [{ id: "n2", parent_id: "n1", position: "a0", content: "", properties: { ink_url: "/api/uploads/ffff.ink?x=1" }, children: [] }] }] });
  assert.deepEqual(Object.keys(snap).sort(), ["n1", "n2", P].sort());
  assert.deepEqual(treeOf(snap, P).map((n) => [n.id, n.children.map((c) => c.id)]), [["n1", ["n2"]]]);
  assert.deepEqual([...uploadRefs(Object.values(snap))].sort(), ["abc123.png", `${"d".repeat(24)}.pdf`, "ffff.ink"]);
});

test("a page's view: a PDF's ink by page, a notebook's sheets with their ink", () => {
  const pdf = { [P]: b("root", "a0", "Paper", { doc_id: "e".repeat(24), source_url: `/api/uploads/${"e".repeat(24)}.pdf` }),
    g1: b(P, "a0", "", { ink_url: "/api/uploads/1.ink", pdf_page: 2 }), n1: b(P, "a1", "note") };
  const v = pageView(pdf, P);
  assert.equal(v.kind, "pdf");
  assert.deepEqual(v.pdfInk, { 2: [{ id: "g1", url: "/api/uploads/1.ink" }] });
  const sheet = newSheet(firstSheetId("bk"), { pattern: "grid" });
  const book = { bk: b("root", "a0", "Notebook", { notebook: { sheet: { pattern: "dots" } } }),
    [sheet.id]: b("bk", "a0", "", sheet.properties), g2: b(sheet.id, "a0", "", { ink_url: "/api/uploads/2.ink" }) };
  const nv = pageView(book, "bk");
  assert.equal(nv.kind, "notebook");
  assert.equal(nv.notebook.paper.pattern, "dots");
  assert.deepEqual(nv.notebook.sheets.map((s) => [s.number, s.paper.pattern, s.ink.map((i) => i.id)]), [[1, "grid", ["g2"]]]);
  assert.deepEqual(libraryRows([{ id: "bk", content: "Notebook", props: book.bk.props, position: "a1" },
    { id: P, content: "", props: pdf[P].props, position: "a0" }]).map((r) => [r.id, r.kind, r.title]),
  [[P, "pdf", "Untitled"], ["bk", "notebook", "Notebook"]]);
  assert.equal(newCanvasInk(10, 20).space.kind, "canvas");
});

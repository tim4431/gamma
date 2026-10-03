// node --test tests/replica.test.mjs (from frontend/) — the iPad replica's
// pure core (src/replica/*): the tree rules against the Python engine's
// cases (tests/shared/synctree.json), the local merges a replica makes
// without a server, the edit-beats-delete rule, the views, and the folder
// and label trees through rounds against a remote in memory. The rounds
// against a real server are the e2e group `replica`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { encodeStroke, newCanvasInk, newInk } from "../src/ink/ink.js";
import { firstSheetId, newSheet } from "../src/notebook/notebook.js";
import { editPage } from "../src/replica/edits.js";
import { ownEdits, reconcileRemoteOps, split, unlanded } from "../src/replica/reconcile.js";
import { syncRound } from "../src/replica/round.js";
import { TREES, apply, applyLocal, childrenOf, diff, moved, snapshotFromTree, treeOf, uploadRefs } from "../src/replica/tree.js";
import { libraryRows, pageView } from "../src/replica/views.js";
import { MemoryHost } from "./replica/memoryHost.mjs";

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
    g1: b(P, "a1", "", { ink_url: uHere, ink_strokes: 2 }) };
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

test("applying another side's change of a text box here keeps the keys this copy changed", async () => {
  const box = { x: 10, y: 10, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  const here = { [P]: b("root", "a0", "Page"), t: b(P, "a0", "Hello", { text_box: { ...box, x: 200 }, pdf_page: 1 }) };
  // the other side typed from the box as it was: its measured width comes over, the move made here stays
  const ops = [{ op: "set", id: "t", content: "Hello world", base: "Hello", props: { text_box: { ...box, w: 101.5 } },
    base_props: { text_box: box } }];
  const { snapshot, applied } = await applyLocal(here, ops, inkStore());
  assert.deepEqual(snapshot.t.props.text_box, { ...box, x: 200, w: 101.5 });
  assert.deepEqual(applied[0].props.text_box, { ...box, x: 200, w: 101.5 }, "the echo names the merged box");
  // and one that names no base box replaces it, as the server does
  const whole = await applyLocal(here, [{ op: "set", id: "t", props: { text_box: { ...box, w: 101.5 } } }], inkStore());
  assert.deepEqual(whole.snapshot.t.props.text_box, { ...box, w: 101.5 });
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

test("a page's view: a PDF's ink by page, a page's sheets with their ink", () => {
  const pdf = { [P]: b("root", "a0", "Paper", { doc_id: "e".repeat(24) }),
    g1: b(P, "a0", "", { ink_url: "/api/uploads/1.ink", pdf_position: { pageNumber: 2 } }), n1: b(P, "a1", "note") };
  const v = pageView(pdf, P);
  assert.equal(v.kind, "pdf");
  assert.deepEqual(v.pdf, { docId: "e".repeat(24), url: `/api/uploads/${"e".repeat(24)}.pdf`, name: "" },
    "the stored copy's URL, derived from doc_id");
  assert.deepEqual(v.pdfInk, { 2: [{ id: "g1", url: "/api/uploads/1.ink" }] });
  assert.deepEqual(v.sheets, [], "a PDF's page has its PDF to read");
  const sheet = newSheet(firstSheetId("bk"), { pattern: "grid" });
  const book = { bk: b("root", "a0", "Notebook", {}), [sheet.id]: b("bk", "a0", "", sheet.properties),
    g2: b(sheet.id, "a0", "", { ink_url: "/api/uploads/2.ink" }), t1: b("bk", "a1", "text"),
    t2: b("t1", "a0", "under"), p2: b("t2", "a0", "", { sheet: {} }) };
  const nv = pageView(book, "bk");
  assert.equal(nv.kind, "page", "a notebook is a page with sheets");
  assert.deepEqual(nv.sheets.map((s) => [s.id, s.number, s.paper.pattern, s.ink.map((i) => i.id)]),
    [[sheet.id, 1, "grid", ["g2"]], ["p2", 2, "blank", []]], "in document order, at any depth");
  assert.deepEqual(libraryRows([{ id: "bk", content: "Notebook", props: book.bk.props, position: "a1" },
    { id: P, content: "", props: pdf[P].props, position: "a0" }]).map((r) => [r.id, r.kind, r.title]),
  [[P, "pdf", "Untitled"], ["bk", "page", "Notebook"]]);
  assert.equal(newCanvasInk(10, 20).space.kind, "canvas");
});

// A remote in memory: as much of the server's API as a round calls, over
// snapshots (pages and the two trees), ops applied as sent, a change log
// whose seq is each page's seq too. It refuses what the server refuses with
// the trees: a page made under a reserved id, a tree deleted. `calls` keeps
// every request as "METHOD path".
function memoryRemote(pages) {
  const store = new Map(Object.entries(pages)), changes = new Map(), calls = [];
  let seq = 0;
  const touch = (id, deleted = false) => changes.set(id, { seq: ++seq, deleted });
  for (const id of store.keys()) touch(id);
  const node = (snap, id) => ({ id, parent_id: snap[id].parent, position: snap[id].position, content: snap[id].content,
    properties: snap[id].props, children: (childrenOf(snap).get(id) || []).map((c) => node(snap, c)) });
  const answer = (method, path, body) => {
    const url = new URL(path, "http://remote"), at = url.pathname;
    let m;
    if (at === "/api/sync/whoami") return [200, { user: "me", workspace: { id: "ws" }, role: "editor", scope: "write" }];
    if (at === "/api/sync/changes") {
      const since = Number(url.searchParams.get("since") || 0);
      const listed = [...changes].filter(([, c]) => c.seq > since).sort((x, y) => x[1].seq - y[1].seq);
      return [200, { more: false, cursor: String(seq),
        pages: listed.filter(([, c]) => !c.deleted).map(([id, c]) => ({ id, seq: c.seq })),
        deleted: listed.filter(([, c]) => c.deleted).map(([id]) => ({ id, deleted_at: "2026-10-02T00:00:00Z" })) }];
    }
    if ((m = /^\/api\/blocks\/([^/]+)\/subtree$/.exec(at))) {
      const snap = store.get(m[1]);
      return snap ? [200, { block: node(snap, m[1]), seq: changes.get(m[1]).seq }] : [404, { detail: "not found" }];
    }
    if ((m = /^\/api\/pages\/([^/]+)\/ops$/.exec(at))) {
      if (!store.has(m[1])) return [404, { detail: "not found" }];
      store.set(m[1], apply(store.get(m[1]), body.ops));
      touch(m[1]);
      return [200, {}];
    }
    if (at === "/api/pages") {
      if (TREES.includes(body.id)) return [400, { detail: "reserved id" }];
      store.set(body.id, { [body.id]: b("root", "a5", body.title, body.properties || {}) });
      touch(body.id);
      return [201, { id: body.id, position: "a5", content: body.title, properties: body.properties || {} }];
    }
    if ((m = /^\/api\/blocks\/([^/]+)$/.exec(at)) && method === "DELETE") {
      if (TREES.includes(m[1])) return [403, { detail: "reserved" }];
      store.delete(m[1]);
      touch(m[1], true);
      return [200, {}];
    }
    return [404, { detail: `no route ${method} ${at}` }];
  };
  return {
    store, calls,
    ops: (id, ops) => answer("POST", `/api/pages/${id}/ops`, { ops }),
    // a tombstone in the feed for a page it still has (a tree: never, on a real server)
    tombstone: (id) => touch(id, true),
    request: async (method, path, body) => {
      calls.push(`${method} ${path.split("?")[0]}`);
      const [status, out] = answer(method, path, body);
      return { status, body: JSON.parse(JSON.stringify(out)) };
    },
  };
}

// A replica host in memory (tests/replica/memoryHost.mjs) whose requests go
// to `remote`.
function hostOf(remote) {
  const host = new MemoryHost({ base: "http://remote", token: "t", remoteWs: "ws" });
  host.request = remote.request;
  return host;
}

const roots = (host) => [...host.pages].map(([id, s]) => ({ id, content: s[id].content, props: s[id].props, position: s[id].position }));
const treeRoot = () => b(null, "a1", "", {});
const trees = () => ({
  folders: { folders: treeRoot(), phys: b("folders", "a0", "Physics"), qec: b("phys", "a0", "QEC / codes, v2"),
    math: b("folders", "a1", "Math") },
  labels: { labels: treeRoot(), read: b("labels", "a0", "to read") },
});

test("a round reconciles the trees first and pulls each whole; the library names folders and labels from them", async () => {
  const { folders, labels } = trees();
  const remote = memoryRemote({
    apage: { apage: b("root", "a0", "Filed", { folders: ["qec", "math", "gone"], labels: ["read", "gone"] }) },
    zpage: { zpage: b("root", "a1", "Unfiled") },
    folders, labels,
  });
  const host = hostOf(remote);
  const status = await syncRound(host);
  assert.equal(status.last_error, "");
  assert.deepEqual(remote.calls.filter((c) => c.endsWith("/subtree")),
    ["GET /api/blocks/folders/subtree", "GET /api/blocks/labels/subtree", "GET /api/blocks/apage/subtree",
      "GET /api/blocks/zpage/subtree"], "the trees first: the pages are filed in them");
  assert.deepEqual(host.pages.get("folders"), folders, "a tree pulled whole is its snapshot, its root's parent null");
  assert.deepEqual(host.pages.get("labels"), labels);
  assert.deepEqual(libraryRows(roots(host), { folders: host.pages.get("folders"), labels: host.pages.get("labels") }), [
    { id: "apage", title: "Filed", kind: "page", position: "a0", folders: ["Physics / QEC / codes, v2", "Math"], labels: ["to read"] },
    { id: "zpage", title: "Unfiled", kind: "page", position: "a1", folders: [], labels: [] },
  ], "no rows for the trees; paths joined with ' / ', a dangling id naming nothing");
  assert.ok(host.notes.some((n) => n.page_id === "folders" && n.title === "Folders"), "the log names the tree");
});

test("both sides' folders are kept, and a tree is never deleted, either way", async () => {
  const remote = memoryRemote({ page: { page: b("root", "a0", "Page") }, ...trees() });
  const host = hostOf(remote);
  await syncRound(host);
  await editPage(host, "folders", [{ op: "insert", id: "here", parent: "folders", position: "a5", content: "Made here", props: {} }]);
  remote.ops("folders", [{ op: "insert", id: "there", parent: "phys", position: "a5", content: "Made there", props: {} }]);
  await syncRound(host);
  for (const snap of [remote.store.get("folders"), host.pages.get("folders")]) {
    assert.deepEqual([snap.here?.content, snap.there?.content, snap.phys.content], ["Made here", "Made there", "Physics"]);
  }
  assert.ok(remote.calls.includes("POST /api/pages/folders/ops"), "the folder made here went over as ops");

  remote.tombstone("folders");
  remote.tombstone("labels");
  await host.deleteHere("labels");
  const status = await syncRound(host);
  assert.equal(status.last_error, "");
  assert.ok(host.pages.get("folders")?.here, "a tombstone there deletes no tree here");
  assert.deepEqual(host.pages.get("labels"), remote.store.get("labels"), "a tree deleted here comes back from the remote");
  assert.ok(!remote.calls.some((c) => c.startsWith("DELETE")), "nor is one deleted there");
  assert.deepEqual(await host.localChanges(), { pages: [], deleted: [] }, "nothing left to carry");
});

test("a tree the remote lacks is left alone: never created there", async () => {
  const remote = memoryRemote({ page: { page: b("root", "a0", "Page") }, folders: trees().folders });
  const host = hostOf(remote);
  const labels = { labels: treeRoot(), mine: b("labels", "a0", "kept here") };
  await host.writeEdit("labels", labels, 0);
  const status = await syncRound(host);
  assert.equal(status.last_error, "");
  assert.ok(!remote.calls.includes("POST /api/pages"), "no page made under a reserved id");
  assert.ok(!remote.store.has("labels"));
  assert.deepEqual(host.pages.get("labels"), labels, "the tree here stays as it is");
  assert.ok(host.pages.has("folders") && host.pages.has("page"), "the rest of the round went on");
});

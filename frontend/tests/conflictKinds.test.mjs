// node --test tests/  (from frontend/) — the conflict card's kinds
// (src/collaboration/conflictKinds.js): every kind of row the sync engines
// record (a clone's and a Zotero link's) has its words, only the text kinds
// offer a choice, a Zotero kind's sides are Gamma and Zotero, and a drawing
// the remote merged says what each side lost.
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { test } from "node:test";
import { KEPT_HERE_END, MERGE_KIND, conflictPlace, inkMergeLines, isTextual, isZotero, keptHere, kindOf, zoteroReason } from "../src/collaboration/conflictKinds.js";

// The Zotero link's kinds: the engine's literal CONFLICT_KINDS tuple, in
// gamma/zotero_sync.py or whichever zotero_sync_*.py module it is split into.
async function zoteroKinds() {
  const gamma = new URL("../../backend/gamma/", import.meta.url);
  const names = (await readdir(gamma)).filter((name) => /^zotero_sync.*\.py$/.test(name)).sort();
  const engine = (await Promise.all(names.map((name) => readFile(new URL(name, gamma), "utf8")))).join("\n");
  const tuple = engine.match(/^CONFLICT_KINDS\s*=\s*\(([^)]*)\)/m);
  assert.ok(tuple, "the Zotero link's engine declares its CONFLICT_KINDS tuple");
  return [...tuple[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

test("every kind the sync engines record has a title and a sentence, and no other kind is listed", async () => {
  const engine = await readFile(new URL("../../backend/gamma/sync_engine.py", import.meta.url), "utf8");
  const recorded = new Set([...engine.matchAll(/_conflict\(conn, [^,]+, [^,]+, "([a-z_]+)"/g)].map((m) => m[1]));
  assert.ok(recorded.has("merged") && recorded.has("ink_merged") && recorded.has("file"), [...recorded].join(", "));
  const zotero = await zoteroKinds();
  assert.ok(zotero.length >= 8 && zotero.includes("zotero_field"), zotero.join(", "));
  for (const kind of zotero) recorded.add(kind);
  for (const kind of recorded) assert.ok(MERGE_KIND[kind]?.short && MERGE_KIND[kind]?.long, `${kind} has its words`);
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => !recorded.has(kind)), []);
  // a Zotero kind's sides are Gamma and Zotero, a clone's local and remote
  for (const kind of Object.keys(MERGE_KIND)) assert.equal(isZotero({ kind }), zotero.includes(kind), kind);
});

test("only the text kinds offer a choice; a kind this build does not know shows its name", () => {
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => isTextual({ kind })),
    ["merged", "diverged", "zotero_field", "zotero_note", "zotero_annotation", "zotero_file", "zotero_lossy"]);
  // Zotero's texts are the two sides, with no merged one (Gamma's was kept); a lossy edit may be sent anyway
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => MERGE_KIND[kind].two),
    ["zotero_field", "zotero_note", "zotero_annotation", "zotero_file", "zotero_lossy"]);
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => MERGE_KIND[kind].send), ["zotero_lossy"]);
  // a merged drawing is acknowledged: putting this clone's back whole would erase what remote drew meanwhile
  assert.equal(isTextual({ kind: "ink_merged" }), false);
  assert.deepEqual(kindOf({ kind: "from_a_newer_server" }), { short: "from_a_newer_server", long: "", icon: "alert" });
  assert.equal(isZotero({ kind: "zotero_from_a_newer_server" }), true, "a newer server's Zotero kind keeps Zotero's sides");
});

test("a Zotero card shows the row's own sentence: a refusal, a give-up after the tries, an annotation back", async () => {
  // the engine puts why in `result`, with mine and theirs empty or a placeholder
  const gaveUp = { kind: "zotero_rejected", mine: "", theirs: "", result: "Gamma could not apply this after 8 tries: disk I/O error" };
  assert.equal(zoteroReason(gaveUp), gaveUp.result);
  assert.equal(zoteroReason({ kind: "zotero_rejected", result: "Zotero refused this: Invalid field" }), "Zotero refused this: Invalid field");
  const locked = { kind: "zotero_locked", mine: "(deleted here)", result: "carol's annotation is read-only in Zotero: it is back" };
  assert.equal(zoteroReason(locked), locked.result);
  assert.equal(zoteroReason({ kind: "zotero_deleted_edited", mine: "(changed here)", result: "Zotero deleted this item for good" }),
    "Zotero deleted this item for good");
  // a PDF both sides replaced says when Zotero's could not be kept
  const file = { kind: "zotero_file", mine: "a.pdf · 1", theirs: "b.pdf · 2", result: "The PDF was replaced on both sides: Gamma's went to Zotero; Zotero's could not be kept" };
  assert.equal(zoteroReason(file), file.result);
  // a text kind's result is the text in the block or its kind's own sentence: not said twice
  assert.equal(zoteroReason({ kind: "zotero_note", mine: "a", theirs: "b", result: "The note changed on both sides: Gamma's went to Zotero" }), "");
  assert.equal(zoteroReason({ kind: "zotero_field", mine: "Volume: 1", theirs: "Volume: 2", result: "Volume: 2" }), "");
  assert.equal(zoteroReason({ kind: "zotero_rejected" }), "");
  assert.equal(zoteroReason({ kind: "file", theirs: "old server", result: "x" }), "", "a clone's row says it in theirs");
  // a refusal is not always Zotero's: the give-up is Gamma's, so the kind's sentence covers both
  assert.match(MERGE_KIND.zotero_rejected.long, /Gamma could not apply/);
  // the engine writes the reason of each of these kinds in result=
  const gamma = new URL("../../backend/gamma/", import.meta.url);
  const names = (await readdir(gamma)).filter((name) => /^zotero_sync.*\.py$/.test(name));
  const engine = (await Promise.all(names.map((name) => readFile(new URL(name, gamma), "utf8")))).join("\n");
  for (const kind of ["zotero_rejected", "zotero_locked", "zotero_deleted_edited"]) {
    // each call as far as the next one: its arguments, whatever parentheses they hold
    const calls = engine.split("record_conflict(").slice(1).filter((call) => call.split("\n")[0].includes(`"${kind}"`));
    for (const call of calls) assert.match(call.slice(0, 400), /result=/, `${kind}: ${call.slice(0, 120)}`);
  }
});

test("a folder's conflict opens the folder and says so, never the folders tree as a page", () => {
  assert.equal(conflictPlace({ page_id: "folders", block_id: "f1" }), "folder");
  assert.equal(conflictPlace({ page_id: "labels", block_id: "l1" }), "label");
  assert.equal(conflictPlace({ page_id: "p1", block_id: "b1" }), "page");
  const folder = kindOf({ kind: "zotero_field", page_id: "folders", block_id: "f1" });
  assert.doesNotMatch(folder.long, /paper/, folder.long);
  assert.match(folder.long, /folder/);
  assert.match(kindOf({ kind: "zotero_field", page_id: "p1" }).long, /paper's record/);
  assert.equal(kindOf({ kind: "zotero_field", page_id: "folders" }).textual, true, "still a text kind");
});

test("a receive-only link's row says Gamma's side stays in Gamma until the link sends, never on both sides", async () => {
  // the engine's sentence: gamma/zotero_sync_rows.py KEPT_HERE, which every such row's result ends with
  const gamma = new URL("../../backend/gamma/", import.meta.url);
  const names = (await readdir(gamma)).filter((name) => /^zotero_sync.*\.py$/.test(name));
  const engine = (await Promise.all(names.map((name) => readFile(new URL(name, gamma), "utf8")))).join("\n");
  const sentence = engine.match(/^KEPT_HERE = "([^"]*)"/m);
  assert.ok(sentence && sentence[1].endsWith(KEPT_HERE_END), sentence?.[1]);
  const kept = (kind, where = {}) => ({ kind, mine: "a", theirs: "b", result: `Changed on both sides: ${sentence[1]}`, ...where });
  for (const kind of ["zotero_note", "zotero_annotation", "zotero_field"]) {
    assert.equal(keptHere(kept(kind)), true, kind);
    const long = kindOf(kept(kind)).long;
    assert.match(long, /kept in Gamma and go(es)? to Zotero once the link sends changes again/, kind);
    assert.doesNotMatch(long, /both sides,/, kind);
    // a two-way link's row: Gamma's went, so it is on both sides
    assert.match(kindOf({ kind, result: "The note changed on both sides: Gamma's went to Zotero" }).long, /kept on both sides/, kind);
    assert.match(kindOf({ kind, result: "" }).long, /kept on both sides/, kind);
  }
  const folder = kindOf(kept("zotero_field", { page_id: "folders", block_id: "f1" })).long;
  assert.match(folder, /folder/);
  assert.match(folder, /goes to Zotero once the link sends changes again/);
  // the sentence is the card's own (a text kind's result is never shown twice) and a clone's row is not Zotero's
  assert.equal(zoteroReason(kept("zotero_note")), "");
  assert.equal(keptHere({ kind: "merged", result: `x ${KEPT_HERE_END}` }), false);
  // only the kinds that keep Gamma's side have the sentence; the others say what they always say
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => MERGE_KIND[kind].kept), ["zotero_field", "zotero_note", "zotero_annotation"]);
  assert.equal(kindOf(kept("zotero_lossy")).long, MERGE_KIND.zotero_lossy.long);
});

test("a drawing the remote merged says what each side lost, counted", () => {
  const row = (report) => ({ kind: "ink_merged", theirs: JSON.stringify(report) });
  assert.deepEqual(inkMergeLines(row({ clean: false, lost_ours: ["a", "b"], returned: ["c"] })), [
    { side: "mine", text: "The changes made here to 2 strokes were not applied: remote changed them too" },
    { side: "theirs", text: "1 stroke erased on remote came back: it was changed here" },
  ]);
  assert.deepEqual(inkMergeLines(row({ clean: false, lost_ours: ["a"], returned: [] })),
    [{ side: "mine", text: "The change made here to 1 stroke was not applied: remote changed it too" }]);
  assert.deepEqual(inkMergeLines(row({ clean: false, lost_ours: [], returned: ["x", "y"] })),
    [{ side: "theirs", text: "2 strokes erased on remote came back: they were changed here" }]);
  // deletions applied by stroke id alone lose nothing countable; a report this build cannot read says as little
  const plain = [{ side: "result", text: "Merged with changes made on remote meanwhile." }];
  assert.deepEqual(inkMergeLines(row({ clean: false, lost_ours: [], returned: [] })), plain);
  assert.deepEqual(inkMergeLines({ kind: "ink_merged", theirs: "not a report" }), plain);
  assert.deepEqual(inkMergeLines({ kind: "ink_merged", theirs: "" }), plain);
});

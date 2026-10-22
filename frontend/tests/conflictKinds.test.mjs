// node --test tests/  (from frontend/) — the conflict card's kinds
// (src/collaboration/conflictKinds.js): every kind of row the sync engine
// records has its words, only the two text kinds offer a choice, and a
// drawing the remote merged says what each side lost.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { MERGE_KIND, inkMergeLines, isTextual, kindOf } from "../src/collaboration/conflictKinds.js";

test("every kind the sync engine records has a title and a sentence, and no other kind is listed", async () => {
  const engine = await readFile(new URL("../../backend/gamma/sync_engine.py", import.meta.url), "utf8");
  const recorded = new Set([...engine.matchAll(/_conflict\(conn, [^,]+, [^,]+, "([a-z_]+)"/g)].map((m) => m[1]));
  assert.ok(recorded.has("merged") && recorded.has("ink_merged") && recorded.has("file"), [...recorded].join(", "));
  for (const kind of recorded) assert.ok(MERGE_KIND[kind]?.short && MERGE_KIND[kind]?.long, `${kind} has its words`);
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => !recorded.has(kind)), []);
});

test("only the two text kinds offer a choice; a kind this build does not know shows its name", () => {
  assert.deepEqual(Object.keys(MERGE_KIND).filter((kind) => isTextual({ kind })), ["merged", "diverged"]);
  // a merged drawing is acknowledged: putting this clone's back whole would erase what remote drew meanwhile
  assert.equal(isTextual({ kind: "ink_merged" }), false);
  assert.deepEqual(kindOf({ kind: "from_a_newer_server" }), { short: "from_a_newer_server", long: "", icon: "alert" });
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

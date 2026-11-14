// node --test tests/  (from frontend/) — what Settings → Backups › Bucket
// says of an environment's off-site round while it runs (src/settings/offsiteRound.js),
// against the progress gamma/offsite.py's status answers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { offsiteProgressText } from "../src/settings/offsiteRound.js";

test("a round says how far it is: the databases, then the files and their bytes", () => {
  assert.equal(offsiteProgressText({ phase: "databases", done: 3, total: 12, name: "ws-a/pages.db" }), "databases 3 / 12");
  assert.equal(offsiteProgressText({ phase: "uploads", done: 1200, total: 3400, name: "ws-a/x.pdf",
    bytes: { done: 1.2 * 1073741824, total: 8 * 1073741824 } }), "files 1,200 / 3,400 · 1.2 GB / 8.0 GB");
  // boto3's count may run past the file's size for a moment: never past the total
  assert.equal(offsiteProgressText({ phase: "uploads", done: 2, total: 2, bytes: { done: 5000, total: 4096 } }),
    "files 2 / 2 · 4 KB / 4 KB");
  assert.equal(offsiteProgressText({ phase: "uploads", done: 0, total: 3, bytes: { done: 0, total: 0 } }), "files 0 / 3");
});

test("no progress, or a phase with nothing to do, says nothing", () => {
  for (const p of [null, undefined, {}, { phase: "uploads", done: 0, total: 0, bytes: { done: 0, total: 0 } }]) {
    assert.equal(offsiteProgressText(p), "");
  }
});

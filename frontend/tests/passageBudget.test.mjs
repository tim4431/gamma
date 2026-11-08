import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MAX_CHAT_PASSAGES, charCount, fairShares, keptNote, keptTitle, passageShares } from "../src/chat/passageBudget.js";

const n = (v) => v.toLocaleString();

// The server's cases too (gamma/ai_context.py fair_shares, quoted_shares;
// backend/tests/test_shared_fixtures.py): the composer's chip and the server
// must cut a passage at the same character.
const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/passages.json", import.meta.url), "utf8"));

test("a shared budget keeps short passages whole and splits the rest evenly (the shared cases)", () => {
  for (const c of SHARED.shares) assert.deepEqual(fairShares(c.sizes, c.budget), c.shares, c.note);
  for (const c of SHARED.shares) assert.ok(c.shares.reduce((a, b) => a + b, 0) <= c.budget, `${c.note}: never more than the budget`);
});

test("a passage is measured in characters as the server counts them (the shared cases)", () => {
  for (const c of SHARED.texts) {
    const passages = c.texts.map((text) => ({ text, page: 1 }));
    const shares = passageShares(passages, c.budget);
    assert.deepEqual(passages.map((p) => shares.get(p)), c.kept, c.note);
    assert.deepEqual(c.texts.map(charCount), c.total, c.note);
  }
});

test("each chip's passage maps to what the message keeps of it", () => {
  const pdf = { text: "x".repeat(40_000), page: 3 };
  const editor = { text: "y".repeat(500), from: 0, to: 500 };
  const note = { kind: "note", text: "z".repeat(30_000) };
  const shares = passageShares([pdf, undefined, editor, note], 60_000); // no editor selection: undefined is skipped
  assert.equal(shares.get(editor), 500);
  assert.equal(shares.get(pdf), 29_750);
  assert.equal(shares.get(note), 29_750);
  assert.equal(shares.size, 3);
  assert.equal(MAX_CHAT_PASSAGES, 12);
});

test("a chip says what it keeps only when its passage won't go whole", () => {
  assert.equal(keptNote(12_000, 31_400), `first ${n(12_000)} of ${n(31_400)} characters`);
  assert.equal(keptNote(500, 500), "");
  assert.equal(keptNote(undefined, 500), "");
  assert.equal(keptTitle(500, 500, 60_000), "");
  const why = keptTitle(12_000, 31_400, 60_000);
  assert.match(why, new RegExp(`up to ${n(60_000)} characters`));
  assert.match(why, /Settings \/ AI \/ Chat \/ Context size/);
  assert.match(why, new RegExp(`first ${n(12_000)} of ${n(31_400)}\\.$`));
});

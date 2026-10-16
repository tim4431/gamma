import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_CHAT_PASSAGES, fairShares, keptNote, keptTitle, passageShares } from "../src/chat/passageBudget.js";

const n = (v) => v.toLocaleString();

// The same cases as backend/tests/test_ai_passages.py: the composer and
// the server must cut a passage at the same character.
const CASES = [
  [[100, 200], 1000, [100, 200]], // everything fits
  [[31_400], 12_000, [12_000]], // one passage: its first 12,000
  [[500, 40_000, 30_000], 60_000, [500, 29_750, 29_750]], // the short one keeps all; the rest share
  [[100, 100], 151, [75, 76]], // a tie: the remainder goes to the later one
  [[10, 20], 0, [0, 0]],
  [[], 60_000, []],
];

test("a shared budget keeps short passages whole and splits the rest evenly", () => {
  for (const [sizes, budget, want] of CASES) assert.deepEqual(fairShares(sizes, budget), want, `${sizes} in ${budget}`);
  // Never more than the budget, never more than a passage has.
  const shares = fairShares([7, 9_000, 3, 120_000, 45_000], 60_000);
  assert.ok(shares.reduce((a, b) => a + b, 0) <= 60_000);
  assert.deepEqual(shares.slice(0, 3), [7, 9_000, 3]);
  assert.equal(shares[3], shares[4]);
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

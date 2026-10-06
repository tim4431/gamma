import test from "node:test";
import assert from "node:assert/strict";
import { mentionAt, insertMention } from "../src/chat/paperMentions.js";

test("mentions follow the caret, support phrases, and exclude email and completed mentions", () => {
  assert.deepEqual(mentionAt("Compare @cavity readout", 23), { start: 8, end: 23, query: "cavity readout" });
  for (const text of ["ada@example.com", "@“cavity paper” ", "@paper\nnext"]) assert.equal(mentionAt(text, text.length), null);
  assert.equal(mentionAt("@paper", 1, 6), null);
  const text = "Compare @cavity with the open paper";
  const mention = mentionAt(text, 15);
  const result = insertMention(text, mention, "Cavity readout");
  assert.equal(result.text, "Compare @“Cavity readout”  with the open paper");
  assert.equal(result.caret, "Compare @“Cavity readout” ".length);
});

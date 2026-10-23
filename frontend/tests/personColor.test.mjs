// A person's colour (src/collaboration/personColor.js): from their name, the
// same wherever they are drawn — on the page beside the presence of the room
// and beside their comments — so it must not shift between releases.
import test from "node:test";
import assert from "node:assert/strict";
import { personColor } from "../src/collaboration/personColor.js";

test("a name always gives the same one of the eight colours", () => {
  assert.deepEqual(["tim-2", "Tim", "alice", "Anonymous", "张三"].map(personColor), [5, 0, 0, 5, 1]);
  assert.equal(personColor(""), personColor(undefined), "no name is one colour too");
  for (const name of ["a", "bob", "Curious Otter", "x".repeat(200)]) {
    const c = personColor(name);
    assert.ok(Number.isInteger(c) && c >= 0 && c < 8, `${name} → ${c}`);
  }
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { startWait, waitAfterDocument, waitingFor } from "../src/pdf/highlightWait.js";

test("one wait at a time: the block that waits, for its kind alone", () => {
  const wait = startWait("extend", "h1");
  assert.deepEqual(wait, { kind: "extend", id: "h1" });
  assert.equal(waitingFor(wait, "extend"), "h1");
  assert.equal(waitingFor(wait, "move"), null);
  assert.equal(waitingFor(wait, "attach"), null);
  assert.equal(waitingFor(null, "move"), null);
});

test("no id or an unknown kind starts no wait", () => {
  assert.equal(startWait("move", null), null);
  assert.equal(startWait("move", ""), null);
  assert.equal(startWait("join", "h1"), null);
});

test("another page or file ends a move or an extension; a note's ⊕ goes on waiting", () => {
  assert.equal(waitAfterDocument(startWait("move", "h1")), null);
  assert.equal(waitAfterDocument(startWait("extend", "h1")), null);
  const attach = startWait("attach", "n1");
  assert.equal(waitAfterDocument(attach), attach);
  assert.equal(waitAfterDocument(null), null);
});

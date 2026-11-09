// A share-link visitor's display name (src/collaboration/linkName.js): the
// cleaning gamma/auth.py link_name applies on the server, held to the same
// answers by tests/shared/linkname.json, so the name a visitor sees is the
// one their edits are stored under.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { LINK_NAME_MAX, cleanLinkName, randomLinkName } from "../src/collaboration/linkName.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/linkname.json", import.meta.url), "utf8"));

test("a name is cleaned as the server cleans it (the shared cases)", () => {
  for (const c of SHARED.clean) assert.equal(cleanLinkName(c.in), c.out, c.note);
});

test("a cleaned name always goes into a header", () => {
  for (const c of SHARED.clean) assert.doesNotThrow(() => encodeURIComponent(cleanLinkName(c.in)), c.note);
  assert.equal(cleanLinkName(undefined), "");
});

test("a minted name is two words, clean and within the cap", () => {
  for (let i = 0; i < 20; i++) {
    const name = randomLinkName();
    assert.match(name, /^[A-Z][a-z]+ [A-Z][a-z]+$/);
    assert.equal(cleanLinkName(name), name);
    assert.ok(name.length <= LINK_NAME_MAX);
  }
});

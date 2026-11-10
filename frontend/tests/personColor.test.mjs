// A person's colour (src/collaboration/personColor.js): from their name, the
// same wherever they are drawn — on the page beside the presence of the room
// and beside their comments, and in the notes PDF's comment cards
// (gamma/pdf_cards.py person_color, held to the same names by
// tests/shared/personcolor.json) — so it must not shift between releases.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { personColor } from "../src/collaboration/personColor.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/personcolor.json", import.meta.url), "utf8"));

test("a name always gives the same one of the eight colours (the shared cases)", () => {
  for (const c of SHARED.names) assert.equal(personColor(c.name), c.index, c.note);
  assert.equal(personColor(""), personColor(undefined), "no name is one colour too");
  for (const name of ["a", "bob", "Curious Otter", "x".repeat(200)]) {
    const c = personColor(name);
    assert.ok(Number.isInteger(c) && c >= 0 && c < 8, `${name} → ${c}`);
  }
});

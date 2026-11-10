// Item types (meta.kind) under Zotero's names: the interface's copy of the
// server's table (gamma/item_types.py), held to the same answers by
// tests/shared/itemtypes.json, the popover rows each type shows, and the
// unverified-metadata rule that depends on the type.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ITEM_TYPES, isCheckableType, itemTypeFields, itemTypeLabel, itemTypeOf, normalizeItemType } from "../src/shared/lib/itemTypes.js";
import { metadataToDraft } from "../src/library/libraryUtils.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/itemtypes.json", import.meta.url), "utf8"));

test("the types in the popover's order, and those a registry can confirm, are the server's", () => {
  assert.deepEqual(ITEM_TYPES.map(([type]) => type), SHARED.types);
  assert.deepEqual(SHARED.types.filter(isCheckableType), SHARED.checkable);
});

test("stored kinds are read as Zotero's item types, the old ones included (the shared cases)", () => {
  for (const c of SHARED.normalize) assert.equal(normalizeItemType(c.in), c.out, c.note);
});

test("a record naming no type is read from its fields, as the server reads it (the shared cases)", () => {
  for (const c of SHARED.item_type) assert.equal(itemTypeOf(c.meta), c.type, c.note);
  assert.equal(metadataToDraft({ kind: "thesis", title: "T" }).kind, "thesis");
  assert.equal(itemTypeLabel("bookSection"), "Book chapter");
});

test("each type shows its own rows and keeps any other field that holds a value", () => {
  const fields = (type, values) => itemTypeFields(type, values).map(([label, field]) => `${label}:${field}`);
  assert.deepEqual(fields("thesis", {}), ["University:publisher", "Year:year"]);
  assert.deepEqual(fields("bookSection", {}), ["Book title:venue", "Year:year", "Pages:pages", "Publisher:publisher", "ISBN:isbn"]);
  // Switched from a journal article to a book: the venue and volume stay visible.
  assert.deepEqual(fields("book", { venue: "Nature", volume: "521", pages: "" }),
    ["Publisher:publisher", "Year:year", "ISBN:isbn", "Venue:venue", "Volume:volume"]);
});

// The rule utils.js's isUnverifiedPaperMeta applies to a record stored
// before the server kept a flag: the stored kind, read as an item type, is
// one a registry holds (a missing or old "paper" kind counts as one).
test("only types a registry holds are checkable, the old and missing kinds included", () => {
  const checkable = (kind) => isCheckableType(itemTypeOf({ kind }));
  assert.ok(checkable(undefined) && checkable("paper") && checkable("conferencePaper") && checkable("book"));
  assert.ok(!checkable("notes") && !checkable("slides") && !checkable("thesis") && !checkable("report"));
});

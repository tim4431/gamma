// Item types (meta.kind) under Zotero's names: the interface's copy of the
// server's table (gamma/item_types.py), the popover rows each type shows,
// and the unverified-metadata rule that depends on the type.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isCheckableType, itemTypeFields, itemTypeLabel, itemTypeOf, normalizeItemType } from "../src/shared/lib/itemTypes.js";
import { metadataToDraft } from "../src/library/libraryUtils.js";

test("stored kinds are read as Zotero's item types, the old ones included", () => {
  assert.equal(normalizeItemType("conferencePaper"), "conferencePaper");
  assert.equal(normalizeItemType("BookSection"), "bookSection");
  assert.equal(normalizeItemType("notes"), "document");
  assert.equal(normalizeItemType("slides"), "presentation");
  assert.equal(normalizeItemType("paper"), "");
  assert.equal(normalizeItemType("podcast"), "");
});

test("a record naming no type is read from its fields, as the server reads it", () => {
  assert.equal(itemTypeOf({ publisher: "Wiley" }), "book");
  assert.equal(itemTypeOf({ arxiv_id: "1706.03762", venue: "arXiv:1706.03762" }), "preprint");
  assert.equal(itemTypeOf({ kind: "paper", arxiv_id: "1602.03837", venue: "Phys. Rev. Lett." }), "journalArticle");
  assert.equal(itemTypeOf(null), "journalArticle");
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

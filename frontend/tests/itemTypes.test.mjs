// Item types (meta.kind) under Zotero's names: the interface's copy of the
// server's table (gamma/item_types.py), held to the same answers by
// tests/shared/itemtypes.json, the popover rows each type shows, and the
// unverified-metadata rule that depends on the type.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ITEM_TYPES, isCheckableType, itemTypeFields, itemTypeLabel, itemTypeOf, metaDate, normalizeItemType } from "../src/shared/lib/itemTypes.js";
import { metadataToDraft } from "../src/library/libraryUtils.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/itemtypes.json", import.meta.url), "utf8"));
const DATES = JSON.parse(readFileSync(new URL("../../tests/shared/citationdates.json", import.meta.url), "utf8"));

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
  assert.deepEqual(fields("thesis", {}), ["University:publisher", "Date:date", "URL:url"]);
  assert.deepEqual(fields("journalArticle", {}),
    ["Journal:venue", "Journal abbr.:journal_abbr", "Date:date", "Volume:volume", "Issue:issue", "Pages:pages", "URL:url"]);
  // Switched from a journal article to a book: the venue, volume, issue
  // and abbreviation stay visible, the address last.
  assert.deepEqual(fields("book", { venue: "Nature", volume: "521", issue: "7553", journal_abbr: "Nat.", pages: "" }),
    ["Publisher:publisher", "Date:date", "ISBN:isbn", "Venue:venue", "Journal abbr.:journal_abbr", "Volume:volume", "Issue:issue", "URL:url"]);
});

test("the Date row is a record's date while it is a month or a day of its year, else the year as stored (the shared cases)", () => {
  for (const c of DATES.date_agrees) assert.equal(metaDate(c.meta), c.shown, c.note);
  // the popover's copy edits the Date row, and the save sends it in place of the year
  const draft = metadataToDraft({ title: "T", year: "2020", date: "2020-03", issue: "4", journal_abbr: "Phys. Rev.", url: "https://example.org/a" });
  assert.equal(draft.date, "2020-03");
  assert.ok(!("year" in draft));
  assert.deepEqual([draft.issue, draft.journal_abbr, draft.url], ["4", "Phys. Rev.", "https://example.org/a"]);
  // the Authors row's separator is one a "Last, First" name does not hold
  assert.equal(metadataToDraft({ authors: ["Pereira Campos, Jorge Fernando", "Ada Lovelace"] }).authors,
    "Pereira Campos, Jorge Fernando; Ada Lovelace");
});

// The rule utils.js's isUnverifiedPaperMeta applies to a record stored
// before the server kept a flag: the stored kind, read as an item type, is
// one a registry holds (a missing or old "paper" kind counts as one).
test("only types a registry holds are checkable, the old and missing kinds included", () => {
  const checkable = (kind) => isCheckableType(itemTypeOf({ kind }));
  assert.ok(checkable(undefined) && checkable("paper") && checkable("conferencePaper") && checkable("book"));
  assert.ok(!checkable("notes") && !checkable("slides") && !checkable("thesis") && !checkable("report"));
});

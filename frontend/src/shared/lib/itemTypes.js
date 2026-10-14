// What kind of work a paper record describes (meta.kind), under the names
// Zotero gives its item types: the table gamma/item_types.py keeps on the
// server, mirrored for the metadata popover's Type row, its fields and the
// unverified-metadata warning. Keep the two in sync.

import { T } from "../i18n/i18n.js";

// [type, label] in the popover's order. The labels stay English here and
// are translated where they are shown.
export const ITEM_TYPES = [
  ["journalArticle", T("Journal article")],
  ["conferencePaper", T("Conference paper")],
  ["preprint", T("Preprint")],
  ["bookSection", T("Book chapter")],
  ["book", T("Book")],
  ["thesis", T("Thesis")],
  ["report", T("Report")],
  ["presentation", T("Presentation")],
  ["document", T("Document")],
];
const NAMES = new Map(ITEM_TYPES.map(([type]) => [type.toLowerCase(), type]));

// The kinds stored before Zotero's names. "paper" meant any article, so the
// record's shape resolves it, as it does a record that names no kind.
const LEGACY = { paper: "", notes: "document", slides: "presentation", other: "document" };

// Types a registry can confirm: AI metadata for one of these that nothing
// tied to the PDF is flagged.
const CHECKABLE = new Set(["journalArticle", "conferencePaper", "preprint", "bookSection", "book"]);

// A stored kind as an item type, "" when it names none.
export function normalizeItemType(value) {
  const name = String(value || "").trim().toLowerCase();
  return NAMES.get(name) || LEGACY[name] || "";
}

// The item type of a record: the one it names, else what its fields imply
// (a publisher without a venue is a book, an arXiv id without a published
// venue a preprint, anything else a journal article).
export function itemTypeOf(meta) {
  const named = normalizeItemType(meta?.kind);
  if (named) return named;
  const venue = String(meta?.venue || "").trim();
  if (meta?.publisher && !venue && !meta?.arxiv_id) return "book";
  if (meta?.arxiv_id && (!venue || /^arxiv/i.test(venue))) return "preprint";
  return "journalArticle";
}

export const itemTypeLabel = (type) => ITEM_TYPES.find(([t]) => t === type)?.[1] || T("Document");
export const isCheckableType = (type) => CHECKABLE.has(type);

// The popover's rows after Title and Authors, per type: [label, field].
// Where the venue and the publisher sit is what tells the types apart (a
// chapter's venue is its book, a thesis's publisher its university).
const VENUE = [T("Venue"), "venue"], YEAR = [T("Year"), "year"], VOLUME = [T("Volume"), "volume"];
const PAGES = [T("Pages"), "pages"], PUBLISHER = [T("Publisher"), "publisher"], ISBN = ["ISBN", "isbn"];
const TYPE_FIELDS = {
  journalArticle: [[T("Journal"), "venue"], YEAR, VOLUME, PAGES],
  conferencePaper: [[T("Proceedings"), "venue"], YEAR, PAGES, PUBLISHER],
  preprint: [VENUE, YEAR],
  bookSection: [[T("Book title"), "venue"], YEAR, PAGES, PUBLISHER, ISBN],
  book: [PUBLISHER, YEAR, ISBN],
  thesis: [[T("University"), "publisher"], YEAR],
  report: [[T("Institution"), "publisher"], YEAR, PAGES],
  presentation: [VENUE, YEAR],
  document: [VENUE, YEAR],
};
const ALL_FIELDS = [VENUE, VOLUME, PAGES, PUBLISHER, ISBN];

// The rows for a type, then any other field that carries a value, so
// switching the type never hides what the record holds.
export function itemTypeFields(type, values) {
  const own = TYPE_FIELDS[type] || TYPE_FIELDS.document;
  const shown = new Set(own.map(([, field]) => field));
  return [...own, ...ALL_FIELDS.filter(([, field]) => !shown.has(field) && String(values?.[field] || "").trim())];
}

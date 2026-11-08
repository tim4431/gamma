// Gamma's search qualifiers (SearchPanel.jsx): the keys the box knows,
// what each one asks of a page of the library listing, which result
// groups and block kinds `in:` keeps, and the rows of the box's dropdown.
// Pure, so node tests it (tests/searchFilters.test.mjs); the grammar is
// search/queryParse.js.
//
// A page-level qualifier is evaluated here, against the listing (GET
// /blocks/root/children: every page with its filing, pin, dates and
// paper metadata), through the libraryUtils reads the filter chips use.
// The panel sends the matching page ids to /block-search and
// /pdf-search, so the server still ranks and limits inside them. Dates
// resolve in the reader's local time, which the server does not know.
//
// One rule for a known key Gamma cannot apply: a value outside its list,
// a year or date it cannot read, a label or folder no name matches, no
// value at all. The token stays in the free text, as an unknown key's
// does, and `problems` says why (the panel's amber line). A typo then
// finds nothing, or the literal text, never more than was asked for, and
// "In:GaAs" or "in:situ" is still found. The keywords and their values
// are syntax: English in every language, never passed through t().

import { t } from "../shared/i18n/i18n.js";
import { filedIn, folderEntries, folderSubtree, pageAttachment, parseStamp, topPageOf } from "../library/libraryUtils.js";
import { cutSpans, lastAlternative, parseQuery, quoteValue, tokenAt, valueHead, valueTail } from "./queryParse.js";

// What `in:` names, and the block kinds each part asks the server for
// (the stored `kind`, db.BLOCK_HOT_COLUMNS). "pdf" is the open PDF's find
// and the library's PDF group, which hold no blocks.
const IN_KINDS = {
  title: ["page"],
  notes: ["note", "text_box", "picture", "sheet", "recording", "ink"],
  highlights: ["highlight"],
  links: ["link"],
  pdf: [],
};
export const SOURCES = Object.keys(IN_KINDS);

// The forms a year and a date take, as the dropdown and a problem name them.
const YEAR_FORMS = "2020, >=2020, <2015, 2019..2021";
const DATE_FORMS = "2025, 2025-03, 2025-03-14, @today, @today-7d";

// The qualifiers: `type` says how a value is read, `icon` names the
// panel's 14 px icon, `desc` is the dropdown's line for the key, and an
// enum's `values` map each value to its line. A syntax example goes in as
// an argument, so no keyword is ever translated.
export const QUALIFIERS = {
  label: { type: "label", icon: "label", desc: () => t("pages that carry a label") },
  folder: { type: "folder", icon: "folder", desc: () => t("pages filed in a folder or below it") },
  is: {
    type: "enum", icon: "file", desc: () => t("a kind of page: {values}", { values: "pdf, pinned" }), // i18n-ignore: syntax
    values: { pdf: () => t("pages with a PDF"), pinned: () => t("pinned pages") },
  },
  no: {
    type: "enum", icon: "ban", desc: () => t("unlabelled or unfiled pages: {values}", { values: "label, folder" }), // i18n-ignore: syntax
    values: { label: () => t("pages without a label"), folder: () => t("pages filed in no folder") },
  },
  author: { type: "text", icon: "user", desc: () => t("a name among the paper's authors") },
  venue: { type: "text", icon: "book", desc: () => t("the paper's journal or conference") },
  year: { type: "year", icon: "calendar", desc: () => t("the paper's year: {forms}", { forms: YEAR_FORMS }) },
  created: { type: "date", icon: "clock", desc: () => t("when the page was made: {forms}", { forms: DATE_FORMS }) },
  updated: { type: "date", icon: "history", desc: () => t("when the page was last edited: {forms}", { forms: DATE_FORMS }) },
  in: {
    type: "enum", icon: "search", desc: () => t("where the text is searched: {values}", { values: SOURCES.join(", ") }),
    values: {
      title: () => t("page titles"),
      notes: () => t("notes, text boxes, pictures and ink"),
      highlights: () => t("highlights"),
      links: () => t("reference links"),
      pdf: () => t("the text of the PDFs"),
    },
  },
};
export const KEYS = new Set(Object.keys(QUALIFIERS));
// The date presets the dropdown offers after created: or updated:.
const DATE_PRESETS = [
  ["@today", () => t("today")],
  [">=@today-7d", () => t("in the last 7 days")],
  [">=@today-1m", () => t("in the last month")],
  [">=@today-1y", () => t("in the last year")],
  ["<@today-1y", () => t("more than a year ago")],
];

// A name as compared: diacritics and width folded, lowercase, spaces
// collapsed ("Café" is "cafe", "ＰＤＦ" is "pdf").
export const fold = (s) => String(s ?? "").normalize("NFKD").replace(/\p{M}+/gu, "").toLowerCase()
  .replace(/\s+/g, " ").trim();
// A folder path as compared, by its segments: "Physics / Rydberg" and
// "physics/rydberg" read alike.
const pathKey = (s) => String(s).split("/").map(fold).filter(Boolean).join("/");

// A date value as its local-time interval [start, end): 2025 (that year),
// 2025-03 (that month), 2025-03-14 (that day), @today, @today-7d / -2w /
// -3m / -1y (that day, counted back from today). null for anything else.
export function dateInterval(value, now) {
  const v = String(value).trim().toLowerCase();
  let m = /^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$/.exec(v);
  if (m) {
    const y = +m[1], mo = m[2] ? +m[2] - 1 : -1, d = m[3] ? +m[3] : 0;
    if (m[2] && (mo < 0 || mo > 11)) return null;
    if (d) {
      const start = new Date(y, mo, d);
      return start.getMonth() === mo ? [start, new Date(y, mo, d + 1)] : null;
    }
    return mo >= 0 ? [new Date(y, mo, 1), new Date(y, mo + 1, 1)] : [new Date(y, 0, 1), new Date(y + 1, 0, 1)];
  }
  m = /^@today(?:-(\d{1,4})([dwmy]))?$/.exec(v);
  if (!m) return null;
  let y = now.getFullYear(), mo = now.getMonth(), d = now.getDate();
  const n = m[1] ? +m[1] : 0;
  if (m[2] === "d" || m[2] === "w") d -= n * (m[2] === "w" ? 7 : 1);
  else if (m[2] === "m" || m[2] === "y") {
    mo -= m[2] === "m" ? n : 12 * n;
    d = Math.min(d, new Date(y, mo + 1, 0).getDate()); // March 31 less a month is February's last day
  }
  return [new Date(y, mo, d), new Date(y, mo, d + 1)];
}

// One alternative of a year or date clause as a test of a number (a year,
// a time in ms), or null when it cannot be read. `bounds(v)` is a value's
// [start, end): a year's [y, y + 1), a date's interval. A bare value is
// its whole interval; > is after its end, >= from its start, < before its
// start, <= up to its end, a..b from a's start to b's end.
function rangeTest({ op, a, b }, bounds) {
  if (op === "range") {
    const lo = a ? bounds(a) : [-Infinity], hi = b ? bounds(b) : [0, Infinity];
    if (!lo || !hi || (!a && !b)) return null;
    return (x) => x >= lo[0] && x < hi[1];
  }
  const span = bounds(a);
  if (!span) return null;
  const [start, end] = span;
  return {
    "=": (x) => x >= start && x < end, ">": (x) => x >= end, ">=": (x) => x >= start,
    "<": (x) => x < start, "<=": (x) => x < end,
  }[op];
}
const yearBounds = (v) => (/^\d{4}$/.test(v) ? [+v, +v + 1] : null);

// A clause as {test} (a page predicate), {sources} (in:) or {problem},
// with `canon`, its applied meaning as a string (the filter's key).
function compile(clause, { tree, pagesById, now }) {
  const spec = QUALIFIERS[clause.key];
  const { key, values } = clause;
  if (!values.length) return { problem: t("{key} needs a value", { key: `${key}:` }) };
  const sign = clause.neg ? "-" : "";
  if (spec.type === "enum") {
    const names = values.map((v) => fold(v.text));
    if (!names.every((n) => n in spec.values)) {
      return { problem: t("{key} takes {values}", { key: `${key}:`, values: Object.keys(spec.values).join(", ") }) };
    }
    if (key === "in") return { sources: new Set(names) };
    const tests = names.map((n) => ({
      pdf: (b) => !!pageAttachment(b),
      pinned: (b) => !!b.properties?.pinned,
      label: (b) => !filedIn(tree.labels, b.properties?.labels).length,
      folder: (b) => !filedIn(tree.folders, topOf(pagesById, b).properties?.folders).length,
    })[n]);
    return { test: (b) => tests.some((f) => f(b)), canon: `${sign}${key}=${[...new Set(names)].sort()}` };
  }
  if (spec.type === "label") {
    const ids = [];
    for (const v of values) {
      const found = [...tree.labels.values()].filter((l) => fold(l.name) === fold(v.text)).map((l) => l.id);
      if (!found.length) return { problem: t("no label is named {name}", { name: v.text }) };
      ids.push(...found);
    }
    return { test: labelTest(tree, ids), canon: `${sign}label=${[...new Set(ids)].sort()}` };
  }
  if (spec.type === "folder") {
    const ids = [];
    for (const v of values) {
      const want = pathKey(v.text);
      const found = want ? [...tree.folders.values()].filter((f) => {
        const have = pathKey(f.path.join("/"));
        return have === want || have.endsWith(`/${want}`);
      }).map((f) => f.id) : [];
      if (!found.length) return { problem: t("no folder is named {name}", { name: v.text }) };
      ids.push(...found);
    }
    return { test: folderTest(tree, pagesById, ids), canon: `${sign}folder=${[...new Set(ids)].sort()}` };
  }
  if (spec.type === "text") {
    const words = values.map((v) => fold(v.text));
    const fieldOf = key === "author" ? (b) => b.properties?.meta?.authors : (b) => b.properties?.meta?.venue;
    return {
      test: (b) => {
        const field = fieldOf(b);
        const have = (Array.isArray(field) ? field : field ? [field] : []).map(fold);
        return words.some((w) => have.some((h) => h.includes(w)));
      },
      canon: `${sign}${key}=${[...new Set(words)].sort()}`,
    };
  }
  const isYear = spec.type === "year";
  const tests = [];
  for (const v of values) {
    const test = rangeTest(v, isYear ? yearBounds : (d) => dateInterval(d, now)?.map((x) => x.getTime()));
    if (!test) {
      return { problem: isYear ? t("{value} is not a year", { value: v.text })
        : t("{value} is not a date: use {forms}", { value: v.text, forms: DATE_FORMS }) };
    }
    tests.push(test);
  }
  const read = isYear ? (b) => parseInt(String(b.properties?.meta?.year ?? ""), 10)
    : (b) => (b[`${key}_at`] ? parseStamp(b[`${key}_at`]).getTime() : NaN);
  return {
    test: (b) => { const x = read(b); return !Number.isNaN(x) && tests.some((f) => f(x)); },
    canon: `${sign}${key}=${values.map((v) => `${v.op}${fold(v.a)}..${fold(v.b)}`).sort()}`,
  };
}

const topOf = (pagesById, b) => topPageOf(pagesById, b.id) || b;
// A label on the page itself; a folder by where the page's top is filed,
// its subfolders included (blocks_store.pages_in_scope, the server's twin).
const labelTest = (tree, ids) => (b) => filedIn(tree.labels, b.properties?.labels).some((id) => ids.includes(id));
function folderTest(tree, pagesById, ids) {
  const below = new Set(ids.flatMap((id) => [...folderSubtree(tree, id)]));
  return (b) => filedIn(tree.folders, topOf(pagesById, b).properties?.folders).some((id) => below.has(id));
}

// The query and the chips as what the panel searches:
// - `text`: the free text, the applied clauses cut out (queryParse.cutSpans);
//   with none, query.trim();
// - `pageTest`: a listing page → whether every page-level clause and chip
//   holds, null when there is none;
// - `sources`: the groups searched (SOURCES, less what in: leaves out);
//   `kinds`: the block kinds the notes search keeps, null for all, [] for
//   none (no notes request);
// - `problems`: [{raw, from, to, reason}] for the clauses searched as text;
// - `clauses`: the applied ones; `key`: their canonical form with the
//   chips', equal for equal filters however they are spelled.
// `chips` are the panel's {kind, id, name}; `ctx` {tree, pagesById, now}.
export function resolveFilters(query, chips = [], { tree, pagesById, now = new Date() }) {
  const applied = [], problems = [], tests = [], canon = [];
  let sources = new Set(SOURCES);
  for (const clause of parseQuery(query, KEYS)) {
    if (clause.type !== "clause") continue;
    const out = compile(clause, { tree, pagesById, now });
    if (out.problem) {
      problems.push({ raw: clause.raw, from: clause.from, to: clause.to, reason: out.problem });
      continue;
    }
    applied.push(clause);
    if (out.sources) {
      sources = new Set([...sources].filter((s) => out.sources.has(s) !== clause.neg));
      continue;
    }
    tests.push(clause.neg ? (b) => !out.test(b) : out.test);
    canon.push(out.canon);
  }
  for (const chip of chips) {
    tests.push(chip.kind === "folder" ? folderTest(tree, pagesById, [chip.id]) : labelTest(tree, [chip.id]));
    canon.push(`chip:${chip.kind}=${chip.id}`);
  }
  if (sources.size < SOURCES.length) canon.push(`in=${[...sources].sort()}`);
  const blockSources = SOURCES.filter((s) => IN_KINDS[s].length);
  const kinds = blockSources.every((s) => sources.has(s)) ? null
    : blockSources.filter((s) => sources.has(s)).flatMap((s) => IN_KINDS[s]);
  return {
    text: cutSpans(query, applied),
    pageTest: tests.length ? (b) => tests.every((test) => test(b)) : null,
    sources, kinds, problems, clauses: applied,
    key: canon.sort().join(" "),
  };
}

// The pages `pageTest` keeps, as {ids, key}: `key` a digest of the sorted
// ids, so a search follows the listing only when membership changes.
// {ids: null, key: ""} without a test.
export function pageSet(pages, pageTest) {
  if (!pageTest) return { ids: null, key: "" };
  const ids = pages.filter(pageTest).map((b) => b.id);
  let hash = 0x811c9dc5; // FNV-1a over the sorted ids
  for (const id of [...ids].sort()) {
    for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 0x01000193);
    hash = Math.imul(hash ^ 0x2c, 0x01000193);
  }
  return { ids, key: `${ids.length}:${(hash >>> 0).toString(36)}` };
}

// The dropdown under the box, best first:
// - with no clause in the query, the labels and folders whose name holds
//   the whole query (a row Tab makes a chip of, as before qualifiers);
// - the keys the word at the caret begins, once it has 3 letters (is:,
//   in: and no: are in the empty box's hint);
// - after a key's colon, its values: label names, folder paths, the
//   enum's values, date presets; an empty year: shows its forms (an
//   `inert` row, nothing to apply).
// Rows: {kind: "label" | "folder", id, name, chip: true} | {kind: "key",
// key, desc} | {kind: "value", key, value, icon, desc?} | {kind: "forms",
// key, desc, inert: true}. `ctx`: {tree, chips}.
export function suggest(query, caret, { tree, chips = [] }) {
  const rows = [];
  const tokens = parseQuery(query, KEYS);
  const q = fold(query);
  if (q && !tokens.some((tok) => tok.type === "clause")) {
    const picked = new Set(chips.map((c) => `${c.kind}:${c.id}`));
    const options = [
      ...[...tree.labels.values()].map((l) => ({ kind: "label", id: l.id, name: l.name })),
      ...folderEntries(tree).map((f) => ({ kind: "folder", ...f })),
    ].sort((a, b) => a.name.localeCompare(b.name));
    rows.push(...options.filter((o) => !picked.has(`${o.kind}:${o.id}`) && fold(o.name).includes(q))
      .slice(0, 6).map((o) => ({ ...o, chip: true })));
  }
  const tok = tokenAt(query, caret);
  const clause = tokens.find((c) => c.type === "clause" && c.from === tok.from);
  if (clause && caret >= clause.valueFrom) {
    rows.push(...valueRows(clause.key, lastAlternative(query.slice(clause.valueFrom, caret)), tree));
    return rows;
  }
  const word = fold(query.slice(tok.from, caret).replace(/^[-－]/, ""));
  if (word.length >= 3 && /^\p{L}+$/u.test(word) && !/[:：]/.test(tok.raw)) {
    for (const key of KEYS) {
      if (key.startsWith(word)) rows.push({ kind: "key", key, desc: QUALIFIERS[key].desc() });
    }
  }
  return rows;
}

function valueRows(key, typed, tree) {
  const spec = QUALIFIERS[key];
  const want = fold(typed);
  if (spec.type === "label") {
    return [...tree.labels.values()].filter((l) => fold(l.name).includes(want))
      .sort((a, b) => a.name.localeCompare(b.name)).slice(0, 6)
      .map((l) => ({ kind: "value", key, value: l.name, icon: "label" }));
  }
  if (spec.type === "folder") {
    return [...tree.folders.values()].filter((f) => pathKey(f.path.join("/")).includes(pathKey(typed)))
      .map((f) => ({ kind: "value", key, value: f.path.join("/"), label: f.path.join(" / "), icon: "folder" }))
      .sort((a, b) => a.label.localeCompare(b.label)).slice(0, 6);
  }
  if (spec.type === "enum") {
    return Object.entries(spec.values).filter(([v]) => v.startsWith(want))
      .map(([v, desc]) => ({ kind: "value", key, value: v, icon: spec.icon, desc: desc() }));
  }
  if (spec.type === "date") {
    return DATE_PRESETS.filter(([v]) => v.startsWith(typed.trim().toLowerCase()))
      .map(([v, desc]) => ({ kind: "value", key, value: v, icon: spec.icon, desc: desc(), literal: true }));
  }
  if (spec.type === "year" && !want) return [{ kind: "forms", key, desc: YEAR_FORMS, icon: spec.icon, inert: true }];
  return [];
}

// What a row does to the query with the caret at `caret`: {chip} for a
// name row (the panel adds the chip and clears the box), else {query,
// caret}: the token at the caret replaced by the key ("label:") or by the
// clause with the value completed (quoted where it must be) and a space
// after it, the caret past that space. An alternative completed before
// others keeps them, and the caret stops after it.
export function applySuggestion(query, caret, row) {
  if (row.chip) return { chip: { kind: row.kind, id: row.id, name: row.name } };
  const tok = tokenAt(query, caret);
  const neg = /^[-－]/.test(tok.raw) ? "-" : "";
  const before = query.slice(0, tok.from), after = query.slice(tok.to);
  if (row.kind === "key") {
    const insert = `${neg}${row.key}:`;
    return { query: before + insert + after, caret: before.length + insert.length };
  }
  const [clause] = parseQuery(tok.raw, KEYS);
  const at = clause?.type === "clause" ? Math.max(0, caret - tok.from - (clause.valueFrom - clause.from)) : 0;
  const head = clause?.type === "clause" ? valueHead(clause.value.slice(0, at)) : "";
  const tail = clause?.type === "clause" ? valueTail(clause.value, at) : "";
  const insert = `${neg}${row.key}:${head}${row.literal ? row.value : quoteValue(row.value)}`;
  if (tail) return { query: before + insert + tail + after, caret: before.length + insert.length };
  const spaced = /^\s/.test(after);
  return { query: before + insert + (spaced ? "" : " ") + after, caret: before.length + insert.length + 1 };
}

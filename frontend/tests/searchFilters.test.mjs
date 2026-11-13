// Gamma's search qualifiers (search/searchFilters.js): what each asks of a
// listing page, which go to the server as a scope and which as page
// predicates, what in: keeps, the clauses searched as text and why, the
// filter's key and page set, and the dropdown's rows and what they do.
import assert from "node:assert/strict";
import { test } from "node:test";
import { libraryTree } from "../src/library/libraryUtils.js";
import { applySuggestion, dateInterval, pageSet, resolveFilters, suggest } from "../src/search/searchFilters.js";

const node = (id, content, children = []) => ({ id, content, properties: {}, children });
const tree = libraryTree({
  folders: { children: [
    node("phys", "Physics", [node("ryd", "Rydberg", [node("arr", "Arrays")])]),
    node("bio", "Biology", [node("bryd", "Rydberg")]),
    node("deep", "Deep"),
    node("lab", "Lab, renamed / 2026"),
  ] },
  labels: { children: [node("fresh", "Fresh, label"), node("toread", "toread"), node("cafe", "Café")] },
});
// Local times, so the date cases hold in any time zone.
const at = (...parts) => new Date(...parts).toISOString();
// A page's PDF (its `doc`): stored under `hash`, or read from `url`.
const pdfDoc = (hash, url = "") => ({ format: "pdf", name: "", hash, ext: ".pdf", at: url ? "url" : "store", ...(url && { url }) });
const pages = [
  { id: "atom", content: "Atom paper", created_at: at(2025, 2, 14, 12), updated_at: at(2026, 10, 5, 9),
    properties: { doc: pdfDoc("d1"), folders: ["ryd"], labels: ["fresh"],
      meta: { authors: ["Ana Müller", "Bo Li"], venue: "Physical Review Letters", year: "2020" } } },
  { id: "sub", content: "Inside atom", _parentPage: "atom", created_at: at(2025, 2, 15, 0, 30), updated_at: at(2026, 9, 1),
    properties: { labels: ["toread"], folders: ["deep"] } },
  { id: "loose", content: "Loose note", created_at: at(2024, 11, 31, 23, 59), updated_at: at(2026, 10, 6, 8),
    properties: { pinned: "2026-01-01T00:00:00Z" } },
  { id: "deep1", content: "Deep paper", created_at: at(2025, 0, 1, 0, 0), updated_at: at(2025, 0, 1),
    properties: { folders: ["deep"], labels: ["cafe"], doc: pdfDoc("", "https://x/y.pdf"), meta: { year: 2015, venue: "Nature" } } },
  { id: "arr1", content: "Array paper", created_at: at(2023, 5, 1), updated_at: at(2023, 5, 1),
    properties: { folders: ["arr"], meta: { year: "2019" } } },
  { id: "bio1", content: "Bio paper", created_at: at(2022, 1, 1), updated_at: at(2022, 1, 1),
    properties: { folders: ["bryd", "gone-folder"], labels: ["gone-label"] } },
];
const pagesById = new Map(pages.map((p) => [p.id, p]));
const now = new Date(2026, 10, 6, 15, 0); // Friday 6 November 2026, 15:00 local
const ctx = { tree, pagesById, now };
const resolve = (q, chips = []) => resolveFilters(q, chips, ctx);
// The listing pages the whole filter keeps (its scope and its predicates).
const ids = (q, chips) => {
  const { listTest } = resolve(q, chips);
  return listTest ? pages.filter(listTest).map((p) => p.id) : null;
};
const kept = (test) => pages.filter(test).map((p) => p.id);

test("label: by name, folded for case and diacritics; negated; commas are alternatives", () => {
  assert.deepEqual(ids('label:"Fresh, label"'), ["atom"]);
  assert.deepEqual(ids("label:cafe"), ["deep1"]);
  assert.deepEqual(ids("Label:CAFÉ"), ["deep1"]);
  assert.deepEqual(ids("label:toread,cafe"), ["sub", "deep1"]);
  assert.deepEqual(ids("-label:toread"), ["atom", "loose", "deep1", "arr1", "bio1"]);
  // a sub-page carries its own labels, not its top's
  assert.deepEqual(ids("label:toread"), ["sub"]);
});

test("folder: by its last path segments, subfolders included, a sub-page where its top is filed", () => {
  assert.deepEqual(ids("folder:Rydberg"), ["atom", "sub", "arr1", "bio1"]);
  assert.deepEqual(ids("folder:physics/rydberg"), ["atom", "sub", "arr1"]);
  assert.deepEqual(ids('folder:"Physics / Rydberg"'), ["atom", "sub", "arr1"]);
  assert.deepEqual(ids("folder:physics"), ["atom", "sub", "arr1"]);
  assert.deepEqual(ids("folder:arrays"), ["arr1"]);
  // the sub-page's own folders count for nothing
  assert.deepEqual(ids("folder:deep"), ["deep1"]);
  assert.deepEqual(ids('folder:"Lab, renamed / 2026"'), []);
  assert.deepEqual(ids("-folder:physics"), ["loose", "deep1", "bio1"]);
  assert.deepEqual(ids("folder:deep,biology"), ["deep1", "bio1"]);
});

test("is: and no:, and their negations", () => {
  assert.deepEqual(ids("is:pdf"), ["atom", "deep1"]);
  assert.deepEqual(ids("IS:PDF"), ["atom", "deep1"]);
  assert.deepEqual(ids("is:pinned"), ["loose"]);
  assert.deepEqual(ids("is:pdf,pinned"), ["atom", "loose", "deep1"]);
  assert.deepEqual(ids("-is:pdf"), ["sub", "loose", "arr1", "bio1"]);
  // a document that is no PDF, or one that cannot be read, is no PDF
  const text = { id: "tex", properties: { doc: { format: "text", name: "main.tex", hash: "t1", ext: ".tex", at: "store" } } };
  const unread = { id: "bad", properties: { doc: { format: "pdf", hash: "", ext: ".pdf", at: "store" } } };
  assert.deepEqual([text, unread].filter(resolve("is:pdf").pageTest), []);
  // no is:text: a predicate runs over the listing, which carries no work
  // folder's file, so in:files is the way to the files
  assert.deepEqual(resolve("is:text x").problems.map((p) => p.reason), ["is: takes pdf, pinned"]);
  // a label or folder the tree no longer has is no filing
  assert.deepEqual(ids("no:label"), ["loose", "arr1", "bio1"]);
  assert.deepEqual(ids("no:folder"), ["loose"]);
  assert.deepEqual(ids("-no:folder"), ["atom", "sub", "deep1", "arr1", "bio1"]);
});

test("author: and venue: are folded substrings; a page without meta fails one and passes its negation", () => {
  assert.deepEqual(ids("author:muller"), ["atom"]);
  assert.deepEqual(ids("author:li"), ["atom"]);
  assert.deepEqual(ids('author:"bo li"'), ["atom"]);
  assert.deepEqual(ids("venue:review"), ["atom"]);
  assert.deepEqual(ids("venue:nature,letters"), ["atom", "deep1"]);
  assert.deepEqual(ids("-author:muller"), ["sub", "loose", "deep1", "arr1", "bio1"]);
  assert.deepEqual(ids("-venue:nature"), ["atom", "sub", "loose", "arr1", "bio1"]);
});

test("year: =, ops, ranges and commas over string and number years", () => {
  assert.deepEqual(ids("year:2020"), ["atom"]);
  assert.deepEqual(ids("year:>=2019"), ["atom", "arr1"]);
  assert.deepEqual(ids("year:>2019"), ["atom"]);
  assert.deepEqual(ids("year:<2019"), ["deep1"]);
  assert.deepEqual(ids("year:<=2019"), ["deep1", "arr1"]);
  assert.deepEqual(ids("year:2015..2019"), ["deep1", "arr1"]);
  assert.deepEqual(ids("year:2016.."), ["atom", "arr1"]);
  assert.deepEqual(ids("year:..2019"), ["deep1", "arr1"]);
  assert.deepEqual(ids("year:2015,2020"), ["atom", "deep1"]);
  assert.deepEqual(ids("-year:2020"), ["sub", "loose", "deep1", "arr1", "bio1"]);
});

test("dateInterval: a day, a month, a year, @today and counted back, in local time", () => {
  const day = (iv) => iv.map((d) => [d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours()]);
  assert.deepEqual(day(dateInterval("2025-03-14", now)), [[2025, 3, 14, 0], [2025, 3, 15, 0]]);
  assert.deepEqual(day(dateInterval("2025-3", now)), [[2025, 3, 1, 0], [2025, 4, 1, 0]]);
  assert.deepEqual(day(dateInterval("2025", now)), [[2025, 1, 1, 0], [2026, 1, 1, 0]]);
  assert.deepEqual(day(dateInterval("2024-12", now)), [[2024, 12, 1, 0], [2025, 1, 1, 0]]);
  assert.deepEqual(day(dateInterval("@today", now)), [[2026, 11, 6, 0], [2026, 11, 7, 0]]);
  assert.deepEqual(day(dateInterval("@today-7d", now)), [[2026, 10, 30, 0], [2026, 10, 31, 0]]);
  assert.deepEqual(day(dateInterval("@today-2w", now)), [[2026, 10, 23, 0], [2026, 10, 24, 0]]);
  assert.deepEqual(day(dateInterval("@today-3m", now)), [[2026, 8, 6, 0], [2026, 8, 7, 0]]);
  assert.deepEqual(day(dateInterval("@today-1y", now)), [[2025, 11, 6, 0], [2025, 11, 7, 0]]);
  assert.deepEqual(day(dateInterval("@today-1m", new Date(2026, 2, 31, 9))), [[2026, 2, 28, 0], [2026, 3, 1, 0]]);
  for (const bad of ["2025-13", "2025-02-30", "25", "yesterday", "@today-7", "@today+1d", ""]) {
    assert.equal(dateInterval(bad, now), null, bad);
  }
});

test("created: and updated: as local-time intervals, with the boundaries of > >= < <=", () => {
  assert.deepEqual(ids("created:2025-03-14"), ["atom"]);
  assert.deepEqual(ids("created:2025-03"), ["atom", "sub"]);
  assert.deepEqual(ids("created:2025"), ["atom", "sub", "deep1"]);
  assert.deepEqual(ids("created:>2025-03-14"), ["sub"]);
  assert.deepEqual(ids("created:>=2025-03-14"), ["atom", "sub"]);
  assert.deepEqual(ids("created:<2025"), ["loose", "arr1", "bio1"]);
  assert.deepEqual(ids("created:<=2024-12-31"), ["loose", "arr1", "bio1"]);
  assert.deepEqual(ids("created:2024-12-31..2025-01-01"), ["loose", "deep1"]);
  assert.deepEqual(ids("updated:@today"), ["loose"]);
  assert.deepEqual(ids("updated:>=@today-7d"), ["atom", "loose"]);
  assert.deepEqual(ids("updated:@today-1d"), ["atom"]);
  assert.deepEqual(ids("updated:<@today-1y"), ["deep1", "arr1", "bio1"]);
  assert.deepEqual(ids("-updated:>=@today-7d"), ["sub", "deep1", "arr1", "bio1"]);
});

test("in: keeps groups, kinds and formats; the full set asks for every one; -in:pdf drops the PDFs", () => {
  const all = resolve("atom");
  assert.deepEqual([...all.sources], ["title", "notes", "highlights", "links", "pdf", "files"]);
  assert.equal(all.kinds, null);
  assert.equal(all.formats, null);
  assert.equal(all.pageTest, null);
  assert.equal(all.scope, null);
  const own = resolve("-in:pdf atom");
  assert.deepEqual([...own.sources], ["title", "notes", "highlights", "links", "files"]);
  assert.equal(own.kinds, null);
  assert.deepEqual(own.formats, ["text"]);
  assert.equal(own.text, "atom");
  assert.equal(own.pageTest, null, "in: is no page filter");
  assert.equal(own.listTest, null);
  const hl = resolve("in:highlights atom");
  assert.deepEqual([...hl.sources], ["highlights"]);
  assert.deepEqual(hl.kinds, ["highlight"]);
  assert.deepEqual(hl.formats, [], "no document request");
  assert.deepEqual(resolve("in:notes,links").kinds, ["note", "text_box", "picture", "sheet", "recording", "ink", "link"]);
  assert.deepEqual(resolve("in:title").kinds, ["page"]);
  assert.deepEqual(resolve("in:pdf").kinds, []);
  assert.deepEqual(resolve("in:pdf").formats, ["pdf"]);
  assert.deepEqual([...resolve("in:pdf,notes -in:notes").sources], ["pdf"]);
});

test("in:files is the text files' group: the document search asks for text alone", () => {
  const files = resolve("in:files gradient");
  assert.deepEqual([...files.sources], ["files"]);
  assert.deepEqual(files.kinds, [], "no notes request");
  assert.deepEqual(files.formats, ["text"]);
  assert.equal(files.text, "gradient");
  assert.equal(resolve("in:pdf,files").formats, null);
  assert.deepEqual(resolve("-in:files").formats, ["pdf"]);
  assert.notEqual(resolve("in:files").key, resolve("in:pdf").key);
  assert.deepEqual(suggest("in:fi", 5, { tree }).map((r) => [r.value, r.desc]), [["files", "the text of the files"]]);
});

test("chips join the typed clauses as positive filters by id", () => {
  const deep = { kind: "folder", id: "deep", name: "Deep" };
  const fresh = { kind: "label", id: "fresh", name: "Fresh, label" };
  assert.deepEqual(ids("", [deep]), ["deep1"]);
  assert.deepEqual(ids("", [{ kind: "folder", id: "phys", name: "Physics" }]), ["atom", "sub", "arr1"]);
  assert.deepEqual(ids("is:pdf", [{ kind: "folder", id: "phys", name: "Physics" }]), ["atom"]);
  assert.deepEqual(ids("-is:pdf", [fresh]), []);
  assert.deepEqual(resolve("narwhal", [deep]).text, "narwhal");
});

test("a chip, or a clause naming one folder or label, is the server's scope; the rest are page predicates", () => {
  const deep = { kind: "folder", id: "deep", name: "Deep" };
  const phys = { kind: "folder", id: "phys", name: "Physics" };
  const fresh = { kind: "label", id: "fresh", name: "Fresh, label" };
  // a folder chip alone: its id, and no page test, so no ids travel
  const chip = resolve("atom", [deep]);
  assert.deepEqual(chip.scope, { folders: ["deep"], labels: [] });
  assert.equal(chip.pageTest, null);
  assert.deepEqual(kept(chip.listTest), ["deep1"], "the listing still keeps to it");
  assert.deepEqual(resolve("", [fresh]).scope, { folders: [], labels: ["fresh"] });
  // typed: a folder by its path, a label by its name
  const typed = resolve("folder:physics label:cafe atom");
  assert.deepEqual(typed.scope, { folders: ["phys"], labels: ["cafe"] });
  assert.equal(typed.pageTest, null);
  assert.equal(typed.text, "atom");
  // a predicate alone: a page test, and no scope
  const year = resolve("year:2020");
  assert.equal(year.scope, null);
  assert.deepEqual(kept(year.pageTest), ["atom"]);
  // both: the predicate's pages go as ids, the server keeps them to the scope
  const both = resolve("-is:pdf", [phys]);
  assert.deepEqual(both.scope, { folders: ["phys"], labels: [] });
  assert.deepEqual(kept(both.pageTest), ["sub", "loose", "arr1", "bio1"]);
  assert.deepEqual(kept(both.listTest), ["sub", "arr1"]);
  // the server intersects its ids and has no "not": a clause negated, or
  // naming several (commas, a name two folders share), is a predicate
  for (const q of ["-folder:physics", "folder:deep,biology", "folder:Rydberg", "label:toread,cafe", "-label:toread"]) {
    const f = resolve(q);
    assert.equal(f.scope, null, q);
    assert.deepEqual(kept(f.pageTest), ids(q), q);
  }
  // one name spelled twice is one id; a chip and a clause on one folder too
  assert.deepEqual(resolve("label:cafe,Café").scope, { folders: [], labels: ["cafe"] });
  assert.deepEqual(resolve("folder:deep", [deep]).scope, { folders: ["deep"], labels: [] });
  // the key names the scope as before: an equal filter, however reached
  assert.equal(resolve("folder:deep x").key, resolve("Folder:DEEP y").key);
});

test("a known key it cannot apply is searched as text, and the problem says why", () => {
  const cases = {
    "is:foo x": "is: takes pdf, pinned",
    "year:abc x": "abc is not a year",
    "year:20 x": "20 is not a year",
    "year:.. x": ".. is not a year",
    "created:2025-13 x": "2025-13 is not a date: use 2025, 2025-03, 2025-03-14, @today, @today-7d",
    "label:nosuch x": "no label is named nosuch",
    "folder:nowhere x": "no folder is named nowhere",
    "folder: x": "folder: needs a value",
    "in:situ x": "in: takes title, notes, highlights, links, pdf, files",
    "label:fresh,nosuch x": "no label is named fresh",
  };
  for (const [q, reason] of Object.entries(cases)) {
    const f = resolve(q);
    const raw = q.slice(0, q.indexOf(" "));
    assert.deepEqual(f.problems, [{ raw, from: 0, to: raw.length, reason }], q);
    assert.equal(f.text, q, `${q}: searched as typed`);
    assert.equal(f.pageTest, null, `${q}: no filter`);
    assert.equal(f.listTest, null, `${q}: no filter`);
    assert.equal(f.scope, null, `${q}: no scope`);
  }
  const gaas = resolve("In:GaAs substrate");
  assert.equal(gaas.text, "In:GaAs substrate");
  assert.equal(gaas.sources.size, 6);
  // the clauses that apply still do
  const mixed = resolve("is:pdf year:abc atom");
  assert.equal(mixed.text, "year:abc atom");
  assert.deepEqual(pages.filter(mixed.pageTest).map((p) => p.id), ["atom", "deep1"]);
});

test("key: the same filter however it is spelled; a different one differs", () => {
  const key = (q, chips) => resolve(q, chips).key;
  assert.equal(key("label:cafe is:PDF x"), key("IS:pdf  Label:Café y"));
  assert.equal(key("label:toread,cafe"), key("label:cafe,toread"));
  assert.equal(key("-in:pdf"), key("in:title,notes,highlights,links,files"));
  assert.notEqual(key("label:cafe"), key("-label:cafe"));
  assert.notEqual(key("is:pdf"), key("is:pdf", [{ kind: "label", id: "fresh", name: "Fresh, label" }]));
  assert.equal(key("atom"), "");
  assert.equal(key("year:abc"), "", "a problem is no filter");
});

test("pageSet: the matching ids, its key moving only with membership", () => {
  assert.deepEqual(pageSet(pages, null), { ids: null, key: "" });
  const pdf = resolve("is:pdf").pageTest;
  const a = pageSet(pages, pdf);
  assert.deepEqual(a.ids, ["atom", "deep1"]);
  assert.equal(pageSet([...pages].reverse(), pdf).key, a.key, "the order is no change");
  const edited = pages.map((p) => (p.id === "loose" ? { ...p, content: "Renamed" } : p));
  assert.equal(pageSet(edited, pdf).key, a.key, "an edit outside the set is no change");
  const filed = pages.map((p) => (p.id === "loose" ? { ...p, properties: { doc: pdfDoc("d9") } } : p));
  assert.notEqual(pageSet(filed, pdf).key, a.key);
  assert.notEqual(pageSet(pages, () => false).key, pageSet(pages, () => true).key);
  assert.deepEqual(pageSet(pages, () => false), { ids: [], key: pageSet([], () => true).key });
});

test("suggest: bare names for a clause-free query, key rows after three letters, values after a colon", () => {
  const rows = (q, caret = q.length, chips) => suggest(q, caret, { tree, chips });
  const names = (q, caret, chips) => rows(q, caret, chips).map((r) => r.name ?? r.value ?? (r.key && `${r.key}:`) ?? r.desc);
  assert.deepEqual(rows("fresh").map((r) => [r.kind, r.id, r.chip]), [["label", "fresh", true]]);
  assert.deepEqual(names("fresh", 5, [{ kind: "label", id: "fresh" }]), [], "a chip already picked");
  assert.deepEqual(names("year:2020 fresh"), [], "a clause in the query: no bare names");
  assert.deepEqual(names("lab"), ["Fresh, label", "Lab, renamed / 2026", "label:"], "the key row after the names");
  assert.deepEqual(names("is"), []);
  assert.deepEqual(names("is:"), ["pdf", "pinned"]);
  assert.deepEqual(names("is:pi"), ["pinned"]);
  assert.deepEqual(names("label:fre"), ["Fresh, label"]);
  assert.deepEqual(names("label:CAF"), ["Café"]);
  assert.deepEqual(names("label:toread,fr"), ["Fresh, label"]);
  assert.deepEqual(names("folder:ryd"), ["Biology/Rydberg", "Physics/Rydberg", "Physics/Rydberg/Arrays"]);
  assert.deepEqual(names("folder:physics/ry"), ["Physics/Rydberg", "Physics/Rydberg/Arrays"]);
  assert.deepEqual(names("upd"), ["updated:"]);
  assert.deepEqual(names("-fol"), ["folder:"]);
  assert.deepEqual(names("updated:"), ["@today", ">=@today-7d", ">=@today-1m", ">=@today-1y", "<@today-1y"]);
  assert.deepEqual(names("updated:>="), [">=@today-7d", ">=@today-1m", ">=@today-1y"]);
  assert.equal(rows("updated:")[1].desc, "in the last 7 days");
  assert.deepEqual(rows("year:").map((r) => [r.kind, r.inert, r.desc]), [["forms", true, "2020, >=2020, <2015, 2019..2021"]]);
  assert.deepEqual(names("year:20"), []);
  assert.deepEqual(names("author:"), []);
  // the caret's own token, wherever it is in the query
  assert.deepEqual(names("label:fre attention", 9), ["Fresh, label"]);
  assert.deepEqual(names("attention label:fre", 9), []);
  assert.deepEqual(names("lab:x"), [], "a word with a colon offers no key");
});

test("applySuggestion: a name row is a chip; a keyed row completes the text, quoted, with a space", () => {
  const apply = (q, row, caret = q.length) => applySuggestion(q, caret, row);
  const row = (q, i = 0, caret = q.length) => suggest(q, caret, { tree })[i];
  assert.deepEqual(apply("fresh", row("fresh")), { chip: { kind: "label", id: "fresh", name: "Fresh, label" } });
  assert.deepEqual(apply("fol", row("fol")), { query: "folder:", caret: 7 });
  assert.deepEqual(apply("-fol", row("-fol")), { query: "-folder:", caret: 8 });
  assert.deepEqual(apply("label:fre", row("label:fre")), { query: 'label:"Fresh, label" ', caret: 21 });
  assert.deepEqual(apply("-folder:de", row("-folder:de")), { query: "-folder:Deep ", caret: 13 });
  assert.deepEqual(apply("label:toread,fr", row("label:toread,fr")), { query: 'label:toread,"Fresh, label" ', caret: 28 });
  assert.deepEqual(apply("updated:", row("updated:", 1)), { query: "updated:>=@today-7d ", caret: 20 });
  // mid-query: the token is replaced, the space after it kept
  assert.deepEqual(apply("x is:p y", row("x is:p y", 0, 6), 6), { query: "x is:pdf y", caret: 9 });
  assert.deepEqual(apply("x Folder:phy", row("x Folder:phy")), { query: "x folder:Physics ", caret: 17 });
  // an alternative completed before others keeps them, the caret after it
  const toread = suggest("label:a,b", 7, { tree }).find((r) => r.value === "toread");
  assert.deepEqual(apply("label:a,b", toread, 7), { query: "label:toread,b", caret: 12 });
  assert.deepEqual(apply("x -is:p,pinned y", row("x -is:p,pinned y", 0, 7), 7), { query: "x -is:pdf,pinned y", caret: 9 });
  // what it gives parses back to the clause it offered
  const done = apply("label:fre", row("label:fre")).query;
  assert.deepEqual(resolveFilters(done, [], ctx).problems, []);
});

// node --test tests/  (from frontend/) — the Properties popover's model
// (src/editor/blockProps.js): the kind by the server's rule, the sections a
// block's keys land in, colours, places, times, key and SQL paths, the
// sync status, and the catalog against docs/research/block-properties.md.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { LINK_COLOR } from "../src/shared/model/palette.js";
import { CATALOG, KINDS, blockKind, changedKeys, colorSummary, effectiveKind, headInfo, jsonSources, jsonText, keyPath, kindLabel,
  placeSummary, propertySections, refsToFetch, shortHash, sqlPath, statusChips, timeDisplay, urlDisplay,
  wholeBlock } from "../src/editor/blockProps.js";

const rect = { x1: 60, y1: 312, x2: 480, y2: 330 };
const pos = { pageNumber: 1, width: 612, height: 792, boundingRect: rect, rects: [rect] };
const pic = { url: "/api/uploads/3f2a9c.png", x: 10, y: 20, w: 144, h: 96 };
const block = (properties, extra = {}) => ({ id: "b1", content: "", position: "a0", properties, ...extra });
const ids = (sections) => sections.map((s) => s.id);
const section = (sections, id) => sections.find((s) => s.id === id);
const row = (sections, key) => sections.flatMap((s) => s.rows).find((r) => r.key === key);
const NOW = Date.parse("2026-11-03T14:00:00Z");

// The server's rule, case for case: tests/shared/blockkind.json, which
// backend/tests/test_block_columns.py runs through the kind column and
// blocks_store.block_kind.
const KINDS_SHARED = JSON.parse(await readFile(new URL("../../tests/shared/blockkind.json", import.meta.url), "utf8"));

test("blockKind follows the server's rule (the shared cases)", () => {
  for (const c of KINDS_SHARED.props) assert.equal(blockKind({ properties: c.props }), c.kind, c.note);
  // the dict's own fields say it too, whatever the properties hold
  for (const c of KINDS_SHARED.tree) {
    assert.equal(blockKind({ parent_id: c.parent_id, page_id: c.page_id, properties: c.props }), c.kind, c.note);
  }
  assert.equal(blockKind({ properties: { pdf_position: pos } }, { isPage: true }), "page");
  assert.equal(blockKind({ properties: {} }, { isFolder: true }), "folder");
  assert.equal(blockKind({ properties: {} }, { isLabel: true }), "label");
  for (const kind of ["note", "highlight", "link", "ink", "text_box", "picture", "sheet", "recording", "page", "folder", "label"]) {
    assert.ok(KINDS[kind]?.label, kind);
  }
});

test("the shown kind is the server's while the properties agree, else this tab's", () => {
  const server = { id: "b1", kind: "highlight", properties: { pdf_position: pos } };
  assert.equal(effectiveKind({ properties: { pdf_position: pos } }, server), "highlight");
  assert.equal(effectiveKind({ properties: { pdf_position: pos, link_url: "https://x" } }, server), "link");
  assert.equal(effectiveKind(null, server), "highlight");
  assert.equal(effectiveKind({ properties: {} }, null), "note");
});

test("kindLabel refines a note on a highlight and an area highlight", () => {
  assert.equal(kindLabel(block({ linked_highlight_id: "h1", pdf_position: { pageNumber: 3 } })), "Note on a highlight");
  assert.equal(kindLabel(block({ pdf_position: { ...pos, area: true } })), "Area highlight");
  assert.equal(kindLabel(block({ pdf_position: pos })), "Highlight");
  assert.equal(kindLabel(block({ pdf_position: pos, link_page_id: "p" })), "Link region");
  assert.equal(kindLabel(block({}), "page"), "Page");
});

test("a highlight's keys fill its section, Timing, Block and Technical", () => {
  const hl = block({ color: "#ffe5648c", quote: "the coherence", pdf_position: { ...pos, rects: [rect, rect, rect] }, t0: NOW - 3600e3 });
  const sections = propertySections(hl, { ctx: { now: NOW, lookupBlock: (id) => (id === "b1" ? hl : null) } });
  assert.deepEqual(ids(sections), ["highlight", "timing", "block", "technical"]);
  const own = section(sections, "highlight");
  assert.equal(own.title, "Highlight");
  assert.deepEqual(own.rows.map((r) => r.key), ["color", "quote", "pdf_position"]);
  assert.equal(row(sections, "pdf_position").display.text, "Page 1 · 3 lines");
  assert.equal(row(sections, "pdf_position").display.rects.length, 3);
  assert.deepEqual(row(sections, "pdf_position").nav, { action: "showOnPage", id: "b1", label: "Show on page" });
  // a block not on the open page opens its own page on it, or offers no way
  const far = propertySections(hl, { server: { ...hl, kind: "highlight", page_id: "pZ" }, ctx: { now: NOW } });
  assert.deepEqual(row(far, "pdf_position").nav, { action: "openBlock", id: "b1", pageId: "pZ", label: "Go to block" });
  assert.equal(row(propertySections(hl, { ctx: { now: NOW } }), "pdf_position").nav, undefined);
  assert.equal(row(sections, "color").copy, "#ffe5648c");
  const tech = section(sections, "technical");
  assert.ok(tech.collapsed && tech.dim && tech.disclose);
  assert.equal(tech.rows[0].key, "position");
  assert.equal(section(sections, "block").collapsed, false);
});

test("an unknown key goes under Other keys, open, tagged as not in the catalog", () => {
  const sections = propertySections(block({ x_custom: { a: 1 }, odd: "" }), { ctx: { now: NOW } });
  const other = section(sections, "other");
  assert.ok(other.disclose && !other.collapsed);
  const custom = other.rows.find((r) => r.key === "x_custom");
  assert.equal(custom.type, "json");
  assert.equal(custom.display.preview, "{1 key}");
  assert.equal(custom.display.text, jsonText({ a: 1 }));
  assert.deepEqual(custom.tags, [{ text: "Not in the catalog", tone: null }]);
  assert.equal(other.rows.find((r) => r.key === "odd").display.text, "(empty)");
  // a key named like an Object member is a key like any other
  const members = section(propertySections(block({ constructor: "c", toString: 1, foo: 2 }), { ctx: { now: NOW } }), "other");
  assert.deepEqual(members.rows.map((r) => [r.key, r.label]), [["constructor", "constructor"], ["toString", "toString"], ["foo", "foo"]]);
});

test("old keys and a highlight's pdf_page go to Technical, tagged Old key", () => {
  const sections = propertySections(block({ pdf_position: pos, pdf_page: 4, guide_demo: true, summary: "old" }), { ctx: { now: NOW } });
  const tech = section(sections, "technical");
  for (const key of ["pdf_page", "guide_demo", "summary"]) {
    const r = tech.rows.find((x) => x.key === key);
    assert.ok(r, key);
    assert.deepEqual(r.tags, [{ text: "Old key", tone: null }]);
  }
  assert.ok(!section(sections, "timing"), "the old pdf_page is not shown as the block's page");
  // on a note a pdf_page is its page, under Timing
  assert.equal(row(propertySections(block({ pdf_page: 2 }), { ctx: { now: NOW } }), "pdf_page").display.text, "Page 2");
});

test("import keys go under Imported from and Zotero's under Zotero, both collapsed", () => {
  const sections = propertySections(block({ web_url: "https://example.org/a", markdown_import: "abc123", seeded: "welcome",
    zotero_rdf: { rdf: 1 }, zotero_key: "http://zotero.org/users/1/items/ABC" }), { ctx: { now: NOW } });
  const imported = section(sections, "imported"), zotero = section(sections, "zotero");
  assert.ok(imported.collapsed && zotero.collapsed);
  assert.deepEqual(imported.rows.map((r) => r.key), ["web_url", "markdown_import", "seeded"]);
  assert.deepEqual(zotero.rows.map((r) => r.key), ["zotero_key", "zotero_rdf"]);
  assert.equal(row(sections, "web_url").display.host, "example.org");
  assert.equal(row(sections, "zotero_rdf").type, "json");
});

test("empty sections are dropped, and an unset link key is hidden", () => {
  const sections = propertySections(block({ pdf_position: pos, link_url: "", link_page_id: null }), { ctx: { now: NOW } });
  assert.equal(sections[0].id, "highlight");
  assert.ok(!row(sections, "link_url") && !row(sections, "link_page_id"));
  assert.ok(sections.every((s) => s.rows.length));
  assert.deepEqual(ids(propertySections(block({}, { position: "" }), { ctx: { now: NOW } })), ["block"]);
});

test("a page's record: Document, Paper record and Filing, a dangling folder shown as missing", () => {
  const libTree = { folders: new Map([["f1", { id: "f1", name: "Clocks", path: ["Lab", "Clocks"], children: [] }]]),
    labels: new Map([["l1", { id: "l1", name: "to-read" }]]), top: ["f1"] };
  const page = { id: "pA", parent_id: "root", content: "Ramsey (2024)", properties: {
    doc_id: "d".repeat(24), original_filename: "ramsey.pdf",
    meta: { title: "Ramsey fringes", authors: ["A. Author", "B. Author"], venue: "PRL", year: "2024", doi: "10.1103/PhysRevLett.1", source: "doi", match: "source" },
    bibtex: "@article{ramsey2024,\n title={x}\n}",
    folders: ["f1", "gone"], labels: ["l1"], pinned: "2026-11-03T12:00:00" } };
  const sections = propertySections(page, { ctx: { now: NOW, libTree, isOpenPage: true } });
  assert.deepEqual(ids(sections), ["document", "paper", "filing", "block"]);
  assert.equal(row(sections, "original_filename").display.icon, "paperclip");
  assert.equal(row(sections, "doc_id").display.short, "dddddd…dddd");
  assert.deepEqual(row(sections, "doc_id").nav, { action: "openPdf", url: `/api/uploads/${"d".repeat(24)}.pdf`, label: "Open PDF" });
  assert.equal(row(sections, "meta.title").display.text, "Ramsey fringes");
  assert.equal(row(sections, "meta.authors").display.text, "A. Author, B. Author");
  assert.equal(row(sections, "meta.doi").display.url, "https://doi.org/10.1103/PhysRevLett.1");
  assert.equal(row(sections, "cite_key").display.text, "Automatic: ramsey2024");
  assert.equal(row(sections, "bibtex").type, "code");
  assert.equal(section(sections, "paper").action.action, "editMeta");
  const folders = row(sections, "folders");
  assert.deepEqual(folders.display.chips, [{ id: "f1", name: "Lab / Clocks" }]);
  assert.deepEqual(folders.display.missing, [{ id: "gone", text: "Missing folder", warn: true }]);
  assert.deepEqual(row(sections, "labels").display.chips, [{ id: "l1", name: "to-read" }]);
  assert.equal(row(sections, "pinned").display.text, "Pinned 2h ago");
  // a page has no Page, Order or Content row; its parent is the library
  const blockKeys = section(sections, "block").rows.map((r) => r.key);
  assert.deepEqual(blockKeys.slice(0, 3), ["kind", "id", "parent_id"]);
  assert.equal(row(sections, "parent_id").display.text, "Library");
  assert.ok(!blockKeys.includes("content") && !blockKeys.includes("page_id"));
  assert.ok(!propertySections(page, { ctx: { now: NOW, libTree } }).find((s) => s.id === "paper").action);
  // a share, or a listing that never loaded, holds part of the library or
  // none: an id it lacks is out of sight, not missing
  const empty = { folders: new Map(), labels: new Map(), top: [] };
  for (const ctx of [{ libTree, shareMode: true }, { libTree: empty }]) {
    const shared = propertySections(page, { ctx: { now: NOW, ...ctx } });
    assert.ok(row(shared, "folders").display.missing.every((m) => m.text === "Not visible here" && !m.warn));
  }
  // a hand-edited record's stamp is when it was edited
  const manual = { ...page, properties: { ...page.properties, meta: { ...page.properties.meta, source: "manual", at: "2026-11-03T12:00:00" } } };
  assert.equal(row(propertySections(manual, { ctx: { now: NOW } }), "meta.at").label, "Edited");
  assert.equal(row(propertySections({ ...manual, properties: { ...manual.properties, meta: { ...manual.properties.meta, source: "doi" } } },
    { ctx: { now: NOW } }), "meta.at").label, "Fetched");
  // a flag stored as 1 reads Yes
  assert.equal(row(propertySections(block({ web_clips: 1 }), { ctx: { now: NOW } }), "web_clips").display.text, "Yes");
  // a sub-page names the page it sits inside under Filing, and one deleted
  // with a page names that page; each opens the page and steps into it
  const pages = [{ id: "pA", content: "Ramsey (2024)" }];
  const sub = { id: "pS", parent_id: "trash", content: "Notes", properties: { parent_page: "pA", deleted_at: "2026-11-03T12:00:00", deleted_with: "pA" } };
  const subSections = propertySections(sub, { ctx: { now: NOW, pages } });
  for (const [key, where, label] of [["parent_page", "filing", "Inside page"], ["deleted_with", "deleted", "Went with"]]) {
    const r = row(subSections, key);
    assert.ok(section(subSections, where).rows.includes(r), `${key} is under ${where}`);
    assert.equal(r.label, label);
    assert.equal(r.display.text, "Ramsey (2024)");
    assert.equal(r.step, "pA");
    assert.deepEqual(r.nav, { action: "openPage", id: "pA", label: "Open page" });
  }
});

test("the Block section: server fields, parent, order, the saved state", () => {
  const tree = [{ id: "n0", children: [] }, { id: "b1", children: [] }, { id: "n2", children: [] }];
  const live = block({ t0: 1 }, { content: "first line\nsecond" });
  const ctx = { now: NOW, tree, pageId: "pA", pageTitle: "Page A", lookupBlock: (id) => (id === "b1" ? { ...live, depth: 0, parentId: null } : null) };
  const loading = propertySections(live, { ctx });
  assert.equal(row(loading, "created_at").display.loading, true);
  assert.equal(row(loading, "parent_id").display.text, "Top level of the page");
  assert.equal(row(loading, "order").display.text, "2 of 3 · level 1");
  assert.equal(row(loading, "page_id").display.text, "Page A");
  assert.equal(row(loading, "page_id").step, "pA");
  assert.equal(row(loading, "content").display.text, "first line");
  assert.equal(row(loading, "content").display.detail, "17 characters");
  assert.equal(row(loading, "kind").sqlPath, "kind");
  assert.equal(row(loading, "order").sqlPath, null);
  const server = { ...live, kind: "note", parent_id: "pA", page_id: "pA", created_at: "2026-11-01T14:00:00", updated_at: "2026-11-03T12:00:00" };
  const saved = propertySections(live, { server, ctx });
  assert.match(row(saved, "updated_at").display.text, /^2h ago · saved$/);
  assert.equal(row(saved, "created_at").display.iso, "2026-11-01T14:00:00.000Z");
  const changed = propertySections({ ...live, properties: { t0: 1, collapsed: true } }, { server, ctx });
  assert.match(row(changed, "updated_at").display.text, /changed in this tab$/);
  // a kind this tab changed reads server → here
  const linked = propertySections(block({ pdf_position: pos, link_url: "https://x" }), { server: { ...server, kind: "highlight" }, ctx });
  assert.equal(row(linked, "kind").display.text, "Highlight → Link region");
});

test("block and page references resolve, else ask to be fetched", () => {
  const ctx = { now: NOW, pages: [{ id: "pB", content: "Page B" }], refCache: { gone: { missing: true }, far: { content: "far text", page_title: "Elsewhere", page_root_id: "pC" } } };
  const s = propertySections(block({ pdf_position: pos, link_page_id: "pB", link_block_id: "far" }), { ctx });
  assert.equal(row(s, "link_page_id").display.text, "Page B");
  assert.deepEqual(row(s, "link_page_id").nav, { action: "openPage", id: "pB", label: "Open page" });
  assert.equal(row(s, "link_block_id").display.page, "Elsewhere");
  assert.equal(row(s, "link_block_id").step, "far");
  const missing = propertySections(block({ pdf_position: pos, link_page_id: "gone", link_block_id: "nowhere" }), { ctx });
  assert.deepEqual(row(missing, "link_page_id").tags, [{ text: "Missing page", tone: "warn" }]);
  assert.equal(row(missing, "link_block_id").display.state, "unresolved");
  assert.deepEqual(refsToFetch(missing), ["nowhere"]);
  const note = propertySections(block({ linked_highlight_id: "h1", pdf_position: { pageNumber: 3 } }),
    { ctx: { now: NOW, lookupBlock: (id) => (id === "h1" ? { id, content: "", properties: { quote: "a passage", pdf_position: pos } } : null) } });
  assert.deepEqual(ids(note), ["timing", "block", "technical"]);
  assert.equal(row(note, "linked_highlight_id").display.text, "a passage");
  assert.equal(row(note, "linked_highlight_id").nav.action, "jumpHighlight");
  assert.equal(row(note, "pdf_position.pageNumber").display.text, "Page 3");
});

test("the other kinds' sections", () => {
  const ctx = { now: NOW };
  const ink = propertySections(block({ ink_url: "", ink_strokes: 4, pdf_position: pos }), { ctx: { ...ctx, inkStrokes: () => 5 } });
  assert.equal(row(ink, "ink_url").display.text, "Not uploaded yet");
  assert.equal(section(ink, "ink").rows.find((r) => r.key === "ink_strokes").display.text, "4 strokes (this tab: 5)");
  assert.deepEqual(section(ink, "technical").rows.map((r) => r.key), ["position", "ink_strokes", "pdf_position"]);
  const box = propertySections(block({ text_box: { x: 10, y: 20, w: 200, size: 16, bg: "#fff4b8", zotero: true }, pdf_page: 2 }), { ctx });
  assert.deepEqual(section(box, "text_box").rows.map((r) => r.display.text),
    ["16 pt", "Black · #1f1f1f", "Yellow · #fff4b8", "Auto", "200×28 pt at (10, 20)", "Zotero text annotation", "Page 2"]);
  const noFill = propertySections(block({ text_box: { auto: false } }), { ctx });
  assert.deepEqual([row(noFill, "text_box.bg").display.text, row(noFill, "text_box.auto").display.text], ["No fill", "Fixed"]);
  const picture = propertySections(block({ picture: { ...pic, angle: 15, flip_h: true, opacity: 0.5, locked: true } }), { ctx });
  assert.deepEqual(section(picture, "picture").rows.map((r) => r.key),
    ["picture.url", "picture", "picture.angle", "picture.flip_h", "picture.opacity", "picture.locked"]);
  assert.equal(row(picture, "picture.url").display.thumb, true);
  assert.equal(row(propertySections(block({ picture: { url: "x" } }), { ctx }), "picture.url").display.text, "File missing");
  const sheet = propertySections(block({ sheet: { width: 841.89, height: 595.28, pattern: "grid", spacing: 20 } }), { ctx });
  assert.deepEqual(section(sheet, "sheet").rows.map((r) => r.display.text),
    ["A4 · Landscape · 841.9×595.3 pt", "White · #ffffff", "Grid · 20 pt", "Light gray · #c8d1dc"]);
  const rec = propertySections(block({ recording: { started: NOW - 60e3, state: "paused", by: "tab2", at: NOW - 1000, dur: 1,
    segments: [{ url: "/api/uploads/a.webm", started: NOW - 60e3, dur: 30000, bytes: 2048, mime: "audio/webm" }] } }),
  { ctx: { ...ctx, tabId: "tab1" } });
  assert.deepEqual(ids(rec), ["recording", "parts", "block", "technical"]);
  assert.equal(row(rec, "recording.state").display.text, "Paused · held by another tab");
  assert.equal(row(rec, "recording.segments").display.text, "0:30");
  assert.equal(section(rec, "parts").rows[0].label, "#1");
  assert.match(section(rec, "parts").rows[0].display.name, / · 0:30 · 2 KB · audio\/webm$/);
  assert.deepEqual(section(rec, "technical").rows.map((r) => r.key), ["position", "recording.dur", "recording.by", "recording.at"]);
  // a lease another tab let go stale: the recording was interrupted
  const stale = propertySections(block({ recording: { started: NOW - 60e3, state: "recording", by: "tab2", at: NOW - 7 * 60e3, segments: [] } }),
    { ctx: { ...ctx, tabId: "tab1" } });
  assert.deepEqual(row(stale, "recording.state").display, { text: "Interrupted", tone: null });
  const mineNow = propertySections(block({ recording: { started: NOW - 60e3, state: "recording", by: "tab1", at: NOW - 7 * 60e3, segments: [] } }),
    { ctx: { ...ctx, tabId: "tab1" } });
  assert.deepEqual(row(mineNow, "recording.state").display, { text: "Recording now", tone: "pending" });
  // parts in start order, each with the index it is stored at
  const seg = (url, started) => ({ url, started, dur: 1000, bytes: 10, mime: "audio/webm" });
  const parts = section(propertySections(block({ recording: { started: 1, state: "done",
    segments: [seg("/b.webm", NOW - 1000), { url: "/c.webm", started: NOW }, seg("/a.webm", NOW - 2000)] } }), { ctx }), "parts").rows;
  assert.deepEqual(parts.map((r) => [r.label, r.keyPath, r.value.url, r.display.url]),
    [["#1", "properties.recording.segments[2]", "/a.webm", "/a.webm"], ["#2", "properties.recording.segments[0]", "/b.webm", "/b.webm"]]);
  // a fold state
  assert.equal(row(propertySections(block({ collapsed: false }), { ctx }), "collapsed").display.text, "Unfolded");
});

test("colours: name, hex and alpha; the link colour; the default", () => {
  assert.equal(colorSummary("#ffe5648c").text, "Yellow · #ffe564 · 55%");
  assert.equal(colorSummary(LINK_COLOR).text, "Link colour");
  const none = colorSummary(undefined);
  assert.equal(none.text, "Default (yellow)");
  assert.ok(none.css && none.muted);
  assert.equal(colorSummary("rgba(255, 229, 100, 0.5)").hex, "#ffe564");
  assert.deepEqual(colorSummary(null, { none: "No fill" }), { css: null, hex: null, alpha: null, name: null, text: "No fill", muted: true });
  assert.equal(headInfo(block({ pdf_position: pos })).swatch, colorSummary(undefined).css);
});

test("placeSummary: page only, lines, area, to check, lost, an older PDF, prev and closest", () => {
  assert.equal(placeSummary({ pageNumber: 2 }).text, "Page 2 only (no exact place)");
  assert.equal(placeSummary({ pageNumber: 2 }).box, null);
  const three = placeSummary({ ...pos, rects: [rect, rect, rect] });
  assert.equal(three.text, "Page 1 · 3 lines");
  // a highlight's box is in the frame of the page as drawn then: a share of it
  assert.equal(three.box, "68.6%×2.3% of the page at (9.8%, 39.4%)");
  assert.equal(placeSummary({ pageNumber: 1, boundingRect: rect, rects: [rect] }).box, "420×18 at (60, 312)");
  assert.deepEqual(three.frame, { width: 612, height: 792 });
  assert.equal(placeSummary({ ...pos, area: true }).text, "Page 1 · area");
  const check = placeSummary({ ...pos, approx: true, match: 0.82 });
  assert.ok(check.dashed && !check.hollow);
  assert.deepEqual(check.chips, [{ id: "check", tone: "warn", text: "To check · 82% match" }]);
  const lost = placeSummary({ ...pos, lost: true, approx: true });
  assert.ok(lost.hollow && !lost.dashed);
  assert.deepEqual(lost.chips.map((c) => c.text), ["Lost"]);
  assert.deepEqual(placeSummary({ ...pos, doc_id: "old" }, { currentDocId: "new" }).chips.map((c) => c.text), ["Measured on an older PDF"]);
  assert.deepEqual(placeSummary({ ...pos, doc_id: "same" }, { currentDocId: "same" }).chips, []);
  const moved = placeSummary({ ...pos, lost: true, prev: { pageNumber: 5, rects: [] },
    closest: { pageNumber: 7, width: 612, height: 792, match: 0.63, rects: [rect], quote: "q" } });
  assert.equal(moved.prev, "Before the PDF was replaced: page 5");
  assert.equal(moved.closest, "Closest match: page 7, 63%");
});

test("times: milliseconds and bare server stamps", () => {
  const t0 = timeDisplay(NOW - 3 * 3600e3, NOW);
  assert.match(t0.text, /ago/);
  assert.doesNotMatch(t0.text, /Invalid/);
  assert.equal(t0.relative, "3h ago");
  const stamp = timeDisplay("2026-11-03T10:00:00", NOW);
  assert.equal(stamp.iso, "2026-11-03T10:00:00.000Z");
  assert.equal(stamp.relative, "4h ago");
  assert.equal(timeDisplay("2026-11-03T10:00:00.123456Z", NOW).iso, "2026-11-03T10:00:00.123Z");
  assert.equal(timeDisplay("not a date"), null);
  assert.equal(timeDisplay(""), null);
  const written = row(propertySections(block({ t0: NOW - 60e3 }), { ctx: { now: NOW } }), "t0");
  assert.equal(written.type, "time");
  assert.match(written.display.text, /1m ago$/);
});

test("key paths and SQL paths", () => {
  assert.equal(keyPath(["pdf_position", "rects", 0, "x1"]), "properties.pdf_position.rects[0].x1");
  assert.equal(keyPath(["odd key"]), 'properties["odd key"]');
  assert.equal(keyPath(["a", "b"], ""), "a.b");
  assert.equal(sqlPath(["pdf_position", "rects", 0, "x1"]), "json_extract(properties, '$.pdf_position.rects[0].x1')");
  assert.equal(sqlPath(["odd key"]), "json_extract(properties, '$.\"odd key\"')");
  assert.equal(sqlPath(["it's"]), "json_extract(properties, '$.\"it''s\"')");
  const place = row(propertySections(block({ pdf_position: pos }), { ctx: { now: NOW } }), "pdf_position");
  assert.equal(place.keyPath, "properties.pdf_position");
  assert.equal(place.sqlPath, "json_extract(properties, '$.pdf_position')");
});

test("changedKeys, wholeBlock, jsonText, jsonSources, shortHash, urlDisplay", () => {
  assert.deepEqual(changedKeys({ properties: { a: 1, b: { c: 2 } }, content: "x" }, { properties: { b: { c: 2 }, d: 3 }, content: "y" }),
    ["a", "d", "content"]);
  assert.deepEqual(changedKeys({ properties: { a: { x: 1, y: 2 } } }, { properties: { a: { y: 2, x: 1 } } }), []);
  assert.deepEqual(changedKeys(null, { properties: {} }), []);
  const dict = { children: [{}], properties: { a: 1 }, content: "c", updated_at: "u", created_at: "c0", position: "a0", page_id: "p", parent_id: "p", kind: "note", id: "b1" };
  const whole = wholeBlock(dict);
  assert.deepEqual(Object.keys(whole), ["id", "kind", "parent_id", "page_id", "position", "created_at", "updated_at", "content", "properties"]);
  assert.ok(!("children" in whole));
  for (const v of [{ a: [1, "two", null, { b: true }] }, [], "s", 0]) assert.deepEqual(JSON.parse(jsonText(v)), v);
  assert.equal(jsonText(undefined), "");
  const same = jsonSources({ ...dict, children: [] }, dict);
  assert.deepEqual(same.block, whole);
  assert.equal(same.server, null);
  // a key this tab holds as undefined is no key
  assert.equal(jsonSources({ ...dict, properties: { a: 1, meta_error: undefined } }, dict).server, null);
  assert.equal(effectiveKind({ properties: { a: 1, meta_error: undefined } }, dict), "note");
  assert.equal(effectiveKind({ properties: { pdf_position: pos, x: undefined } }, { kind: "link", properties: { pdf_position: pos } }), "link");
  const ahead = jsonSources({ ...dict, properties: { a: 2 } }, dict);
  assert.deepEqual(ahead.properties, { a: 2 });
  assert.deepEqual(ahead.server, whole);
  assert.equal(shortHash("a1b2c3d4e5f6a7b8c9d0f9e8"), "a1b2c3…f9e8");
  assert.equal(shortHash("Qm3xA_9fL2kP"), "Qm3xA_9fL2kP");
  assert.deepEqual(urlDisplay("https://example.org/a/b?c=1"), { url: "https://example.org/a/b?c=1", host: "example.org", path: "/a/b?c=1", text: "https://example.org/a/b?c=1" });
  assert.equal(urlDisplay("2401.01234").url, "https://arxiv.org/abs/2401.01234");
  assert.equal(urlDisplay("10.1103/PhysRevLett.1").url, "https://doi.org/10.1103/PhysRevLett.1");
  assert.equal(urlDisplay("/api/uploads/x.pdf").host, "");
});

test("status chips: pending and differing, unsaved, deleted, the place's, Zotero, transcript, a failed load", () => {
  const server = { id: "b1", kind: "highlight", properties: { pdf_position: pos } };
  const live = { id: "b1", properties: { pdf_position: { ...pos, approx: true, match: 0.82 }, zotero_readonly: "lossy", transcript: true } };
  assert.deepEqual(statusChips(live, server, null, { differSince: NOW - 1000, readAt: NOW }).map((c) => c.text),
    ["Changes not saved yet", "To check · 82% match", "Not written back to Zotero", "Transcript"]);
  // it differs once a read asked DIFFER_MS after the difference began still finds it
  const late = statusChips(live, server, null, { differSince: NOW - 5000, readAt: NOW })[0];
  assert.deepEqual(late, { id: "differs", tone: "warn", action: "refetch", text: "This tab differs · 3 keys" });
  assert.equal(statusChips(live, server, null, { differSince: NOW - 5000, readAt: NOW - 4500 })[0].id, "pending");
  assert.deepEqual(statusChips(server, server, null, {}), []);
  assert.deepEqual(statusChips(live, null, { status: 404, data: {} }).map((c) => c.id), ["unsaved", "check", "zotero", "transcript"]);
  assert.deepEqual(statusChips(null, null, { status: 404, data: { trashed: { id: "p" } } }).map((c) => c.id), ["deleted"]);
  assert.deepEqual(statusChips({ properties: { deleted_at: "2026-11-01" } }, null, null).map((c) => c.id), ["deleted"]);
  assert.deepEqual(statusChips(server, null, { status: 503 }), [{ id: "failed", tone: "failed", action: "retry", text: "Couldn't load from the server" }]);
  assert.deepEqual(statusChips({ properties: { pdf_position: { ...pos, doc_id: "old" } } }, null, null, { pageAttachId: "new" }).map((c) => c.id), ["older"]);
  assert.deepEqual(statusChips(server, server, null, { gone: true }).map((c) => c.id), ["gone"]);
  // deleted for good while open: gone, not unsaved; a 404 after the server had it is not unsaved either
  assert.deepEqual(statusChips(live, server, { status: 404, data: {} }, { gone: true }).map((c) => c.id), ["gone", "check", "zotero", "transcript"]);
  assert.ok(!statusChips(live, server, { status: 404, data: {} }, {}).some((c) => c.id === "unsaved"));
});

test("the head: kind, swatch and preview", () => {
  assert.deepEqual(headInfo(block({ color: "#ffe5648c", quote: "q", pdf_position: pos })),
    { title: "Highlight", swatch: "#ffe5648c", preview: "q", empty: false });
  assert.equal(headInfo(block({}, { content: "\n  first\nsecond" })).preview, "first");
  assert.equal(headInfo({ id: "p", parent_id: "root", content: "Title", properties: {} }).preview, "Title");
  assert.deepEqual([headInfo(block({})).preview, headInfo(block({})).empty], ["(empty)", true]);
  assert.equal(headInfo(block({ ink_url: "" })).title, "Handwriting");
});

test("every key of docs/research/block-properties.md's catalog has an entry, with a label and a hint", async () => {
  const doc = await readFile(new URL("../../docs/research/block-properties.md", import.meta.url), "utf8");
  const catalog = doc.slice(doc.indexOf("## The catalog"), doc.indexOf("## Is it well defined?"));
  const named = new Set([...catalog.matchAll(/`([a-z_][A-Za-z0-9_]*)[`:]/g)].map((m) => m[1]));
  const keys = ["ink_url", "ink_strokes", "text_box", "picture", "sheet", "recording", "link_url", "link_page_id", "link_block_id",
    "pdf_position", "pdf_page", "quote", "color", "linked_highlight_id", "imported_annot", "annot_stripped", "t0", "transcript",
    "collapsed", "zotero_readonly", "zotero_author", "doc_id", "source_url", "original_filename", "folders", "labels", "pinned",
    "meta", "meta_error", "auto_title", "bibtex", "cite_key", "ppt_cite", "web_url", "web_meta", "web_clips", "markdown_import",
    "notion_id", "aliases", "notability_import", "goodnotes_import", "zotero_key", "zotero_pdf", "zotero_note", "zotero_rdf",
    "zotero_annotation", "zotero_attachments", "zotero_attachment", "seeded", "deleted_at", "deleted_by", "parent_page", "deleted_with",
    "summary", "guide_demo"];
  for (const key of keys) {
    assert.ok(named.has(key), `${key} is named in the doc's catalog`);
    assert.ok(CATALOG[key]?.label && CATALOG[key].hint && CATALOG[key].section, `${key} has a catalog entry`);
  }
  for (const key of ["pdf_position.pageNumber", "pdf_position.rects", "pdf_position.approx", "pdf_position.closest",
    "recording.started", "recording.segments", "recording.dur", "recording.by", "recording.at"]) {
    assert.ok(CATALOG[key]?.label && CATALOG[key].hint, key);
  }
  for (const key of ["summary", "guide_demo"]) {
    assert.equal(CATALOG[key].legacy, true, key);
  }
});

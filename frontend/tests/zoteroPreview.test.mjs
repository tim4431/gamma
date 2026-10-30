// node --test tests/  (from frontend/) — the Zotero link dialog's counts
// (src/settings/zoteroPreview.js): what the library holds, what an existing
// workspace pairs and writes to Zotero (every count the preview answers),
// "at least" while the counts are a lower bound, the download default that
// follows, and the bounded retries of a count Zotero or the server kept busy.
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { test } from "node:test";
import {
  PREVIEW_RETRY_MS, askPreview, intoLines, libraryLine, previewFailure, previewRetryable, suggestedDownload,
} from "../src/settings/zoteroPreview.js";

const GAMMA = new URL("../../backend/gamma/", import.meta.url);

// The keys a dict literal returns, as Python writes them: `"name":`.
const keysOf = (source) => [...source.matchAll(/"([a-z_]+)":/g)].map((m) => m[1]);

// The counts the engine's first-link preview answers into a workspace
// (preview_first_link's return, in whichever zotero_sync_*.py module holds
// it: zotero_sync_first.py).
async function intoCounts() {
  const names = (await readdir(GAMMA)).filter((name) => /^zotero_sync.*\.py$/.test(name)).sort();
  const engine = (await Promise.all(names.map((name) => readFile(new URL(name, GAMMA), "utf8")))).join("\n")
    .replace(/\r\n/g, "\n");
  const body = engine.match(/^def preview_first_link\([\s\S]*?\n    return (\{[\s\S]*?\})\n/m);
  assert.ok(body, "the engine counts a first link");
  return keysOf(body[1]);
}

const library = { items: 2400, notes: 30, attachments: 1900, pdfs: 1930, pdf_bytes: 8.1e9, linked_files: 120, partial: false };

test("what the library holds: its PDFs, their size, the linked files Gamma cannot download", () => {
  assert.match(libraryLine(library), /^1,930 PDFs · .* in Zotero Storage · 120 linked files Gamma cannot download$/);
  assert.match(libraryLine({ ...library, pdfs: 1, linked_files: 0 }), /^1 PDF · .* in Zotero Storage$/);
  assert.equal(libraryLine(null), "");
  assert.equal(libraryLine(false), "");
});

test("a library past what the counts read says at least, and matches nothing in advance", () => {
  const big = { ...library, attachments: 90000, partial: true };
  assert.match(libraryLine(big), /^at least 1,930 PDFs · at least .* in Zotero Storage · at least 120 linked files Gamma cannot download$/);
  const into = intoLines({ ...big, matches: null, to_send: null, to_send_bytes: null, collections_to_add: null, tags_to_add: null,
    notes_to_send: null, highlights_to_send: null }, { mode: "two-way", upload: false });
  assert.equal(into.length, 1);
  assert.match(into[0], /too large to compare .* the first sync matches .* all the same/);
});

test("into a workspace: what pairs, what is added, and every other thing the first rounds send to Zotero", async () => {
  const keys = await intoCounts();
  for (const key of ["matches", "to_send", "to_send_bytes", "collections_to_add", "tags_to_add", "notes_to_send", "highlights_to_send",
    "highlights_new"]) {
    assert.ok(keys.includes(key), `the engine still answers ${key} (${keys.join(", ")})`);
  }
  // each count the engine answers is on screen: a distinct number for each,
  // the highlights as one (those on papers Zotero has the PDF of, and, with
  // uploads on, those on papers whose PDF this link sends: highlights_new)
  const counts = Object.fromEntries(keys.map((key, i) => [key, 101 + i * 111]));
  const text = intoLines({ ...library, ...counts, to_send_bytes: 5 * 1024 * 1024 }, { mode: "two-way", upload: true }).join("\n");
  const highlights = (counts.highlights_to_send || 0) + (counts.highlights_new || 0);
  assert.ok(text.includes(highlights.toLocaleString()), `the highlights are shown: ${text}`);
  for (const key of keys.filter((k) => !["to_send_bytes", "highlights_to_send", "highlights_new"].includes(k))) {
    assert.ok(text.includes(String(counts[key])), `${key} is shown: ${text}`);
  }
  assert.match(text, /5(\.0)? MB/, "and the bytes the new papers' PDFs take");

  const lines = intoLines({ ...library, matches: 412, to_send: 37, to_send_bytes: 210e6, collections_to_add: 3, tags_to_add: 120,
    notes_to_send: 40, highlights_to_send: 812 }, { mode: "two-way", upload: true });
  assert.match(lines[0], /^412 papers match, 37 will be added to Zotero \(.*MB\)$/);
  assert.equal(lines[1], "Also sent to Zotero: 3 folders as collections · 120 tags from your labels on matching papers · "
    + "40 pages' notes as Zotero notes · 812 highlights as annotations");
  // one of each, and nothing to add
  const one = intoLines({ ...library, matches: 1, to_send: 0, to_send_bytes: 0, collections_to_add: 1, tags_to_add: 1, notes_to_send: 1,
    highlights_to_send: 1 }, { mode: "two-way", upload: true });
  assert.deepEqual(one, ["1 paper matches", "Also sent to Zotero: 1 folder as a collection · 1 tag from your labels on matching papers · "
    + "1 page's notes as a Zotero note · 1 highlight as an annotation"]);
  const none = intoLines({ ...library, matches: 0, to_send: 0, to_send_bytes: 0, collections_to_add: 0, tags_to_add: 0, notes_to_send: 0,
    highlights_to_send: 0 }, { mode: "two-way", upload: true });
  assert.deepEqual(none, ["0 papers match"]);
});

test("what goes to Zotero follows the dialog: nothing on a receive-only link, no PDFs without uploads, no notes the key cannot read", () => {
  const p = { ...library, matches: 412, to_send: 37, to_send_bytes: 210e6, collections_to_add: 3, tags_to_add: 120, notes_to_send: 40,
    highlights_to_send: 812 };
  assert.deepEqual(intoLines(p, { mode: "pull", upload: true }), ["412 papers match"]);
  const noUpload = intoLines(p, { mode: "two-way", upload: false });
  assert.equal(noUpload[0], "412 papers match, 37 will be added to Zotero, without their PDFs");
  assert.equal(intoLines({ ...p, to_send_bytes: 0 }, { mode: "two-way", upload: true })[0], "412 papers match, 37 will be added to Zotero");
  const noNotes = intoLines(p, { mode: "two-way", upload: true, notes: false });
  assert.ok(!noNotes[1].includes("notes"), noNotes[1]);
  assert.deepEqual(intoLines(null, { mode: "two-way" }), []);
});

test("highlights on papers whose PDF this link uploads count only with uploads on: they go once their PDF does", () => {
  const p = { ...library, matches: 412, to_send: 37, to_send_bytes: 210e6, collections_to_add: 0, tags_to_add: 0, notes_to_send: 0,
    highlights_to_send: 800, highlights_new: 12 };
  const up = intoLines(p, { mode: "two-way", upload: true });
  assert.match(up[0], /^412 papers match, 37 will be added to Zotero \(.*MB\)$/);
  assert.equal(up[1], "Also sent to Zotero: 812 highlights as annotations");
  // uploads off: those on papers Zotero has the PDF of still go, whatever the setting
  assert.equal(intoLines(p, { mode: "two-way", upload: false })[1], "Also sent to Zotero: 800 highlights as annotations");
  // only new papers' highlights, uploads off: none goes
  assert.deepEqual(intoLines({ ...p, highlights_to_send: 0 }, { mode: "two-way", upload: false }),
    ["412 papers match, 37 will be added to Zotero, without their PDFs"]);
  assert.equal(intoLines({ ...p, highlights_to_send: 0, highlights_new: 1 }, { mode: "two-way", upload: true })[1],
    "Also sent to Zotero: 1 highlight as an annotation");
  // a receive-only link sends none
  assert.deepEqual(intoLines(p, { mode: "pull", upload: true }), ["412 papers match"]);
  // absent (a server that does not split them) or null (past what the server reads) counts as none
  const { highlights_new: _, ...older } = p;
  assert.equal(intoLines(older, { mode: "two-way", upload: true })[1], "Also sent to Zotero: 800 highlights as annotations");
  assert.equal(intoLines({ ...p, highlights_new: null }, { mode: "two-way", upload: true })[1], "Also sent to Zotero: 800 highlights as annotations");
});

test("a large library, or one whose counts stop short, suggests PDFs as their pages open", () => {
  assert.equal(suggestedDownload(null), "all");
  assert.equal(suggestedDownload(false), "all");
  assert.equal(suggestedDownload({ pdfs: 40, partial: false }), "all");
  assert.equal(suggestedDownload({ pdfs: 1001, partial: false }), "open");
  assert.equal(suggestedDownload({ pdfs: 40, partial: true }), "open", "a lower bound: the library may hold many more");
  assert.equal(suggestedDownload({ pdfs: 40 }, { ipad: true }), "open");
});

test("a count the server or Zotero kept busy is asked again a few times, then given up calmly", async () => {
  const busy = Object.assign(new Error("A count for this account is already running: try again in a moment"), { status: 429 });
  const slow = Object.assign(new Error("Zotero is busy: try the count again in a moment"), { status: 503 });
  const bad = Object.assign(new Error("that is not a workspace of yours"), { status: 400 });
  assert.ok(previewRetryable(busy) && previewRetryable(slow) && !previewRetryable(bad) && !previewRetryable(new Error("x")));
  assert.ok(PREVIEW_RETRY_MS.length >= 2 && PREVIEW_RETRY_MS.length <= 5, "a bounded number of tries");
  assert.ok(PREVIEW_RETRY_MS.every((ms, i) => ms >= 1000 && (i === 0 || ms >= PREVIEW_RETRY_MS[i - 1])), "a short backoff that grows");

  // busy twice, then the counts
  const waited = [];
  const sleep = async (ms) => { waited.push(ms); };
  let calls = 0;
  const answer = await askPreview(async () => { calls += 1; if (calls < 3) throw calls === 1 ? busy : slow; return { pdfs: 3 }; }, { sleep });
  assert.deepEqual(answer, { pdfs: 3 });
  assert.equal(calls, 3);
  assert.deepEqual(waited, PREVIEW_RETRY_MS.slice(0, 2));

  // always busy: every try, then the error
  calls = 0;
  waited.length = 0;
  await assert.rejects(askPreview(async () => { calls += 1; throw slow; }, { sleep }), slow);
  assert.equal(calls, PREVIEW_RETRY_MS.length + 1);
  assert.deepEqual(waited, PREVIEW_RETRY_MS);

  // a refusal no wait mends is not asked again
  calls = 0;
  await assert.rejects(askPreview(async () => { calls += 1; throw bad; }, { sleep }), bad);
  assert.equal(calls, 1);

  // the dialog moved on (another library, closed) while it waited: no more tries, no answer
  calls = 0;
  let alive = true;
  const gone = await askPreview(async () => { calls += 1; throw busy; }, { sleep: async () => { alive = false; }, alive: () => alive });
  assert.equal(gone, null);
  assert.equal(calls, 1);

  // what the dialog says once it gives up: Zotero busy is no fault, another refusal says why
  assert.match(previewFailure(slow), /busy.*link without the counts/i);
  assert.match(previewFailure(busy), /busy.*link without the counts/i);
  assert.equal(previewFailure(bad), "Could not count what this library holds: that is not a workspace of yours");
});

// The library rule on the client (documents/library.js): which root pages
// the library lists, and which are library documents. library.json holds it
// and the backend's twin (gamma/work_folders/library.py, and its SQL) to the
// same answers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { STAMPS, follows, inListing, isLibraryPdf, libraryDocument, listed, workFolderOf } from "../src/documents/library.js";

const LIBRARY = JSON.parse(readFileSync(new URL("../../tests/shared/library.json", import.meta.url), "utf8"));

test("each case: listed, in the listing, a library document, a library PDF", () => {
  for (const c of LIBRARY.cases) {
    const block = { properties: c.props };
    assert.equal(listed(block), c.listed, `${c.name}: listed`);
    assert.equal(inListing(block), c.in_listing, `${c.name}: in_listing`);
    assert.equal(libraryDocument(block), c.library_document, `${c.name}: library_document`);
    assert.equal(isLibraryPdf(block), c.library_pdf, `${c.name}: library_pdf`);
  }
});

test("the fixture holds the cases the rule turns on", () => {
  const names = LIBRARY.cases.map((c) => c.name);
  assert.equal(new Set(names).size, names.length, "case names are unique");
  for (const words of ["a stored PDF page", "a copy", "a URL page", "Opened files", "after Add to library", "a note",
    "PDF not in the library", "PDF in the library", "a work folder's note", "a pinned file", "without work_folder",
    "work_folder empty", "a doc that is no object"]) {
    assert.ok(names.some((name) => name.includes(words)), words);
  }
});

test("a work folder is a non-empty string; no properties is a library page", () => {
  assert.equal(workFolderOf({ properties: { work_folder: "W1" } }), "W1");
  assert.equal(workFolderOf({ properties: { work_folder: "" } }), "");
  assert.equal(workFolderOf({ properties: { work_folder: ["W1"] } }), "");
  assert.equal(workFolderOf({}), "");
  assert.equal(workFolderOf(null), "");
  assert.equal(listed({}), true);
  assert.equal(libraryDocument({ properties: [] }), true, "properties that are no object are none");
  assert.deepEqual(STAMPS, ["work_folder", "library", "file_name"]);
});

test("a note of a work folder that pulls follows Gamma; a PDF, or a work folder that does not pull, does not", () => {
  const note = { properties: { work_folder: "W1", file_name: "notes.md", folders: ["D1"] } };
  const pdf = { properties: { ...note.properties, doc: { format: "pdf", name: "a.pdf", hash: "p1", ext: ".pdf", at: "path" } } };
  assert.equal(follows(note, { pull: true, library: false }), true);
  assert.equal(follows(note, { pull: false, library: true }), false);
  assert.equal(follows(note, null), false, "a work folder this copy has no mark of");
  assert.equal(follows(pdf, { pull: true, library: false }), false);
  assert.equal(follows({ properties: { folders: ["L1"] } }, { pull: true }), false, "a library page is no file");
});

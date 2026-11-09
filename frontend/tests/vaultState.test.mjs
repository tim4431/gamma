// The open page's standing with this computer (src/vault/vaultState.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addsToLibrary, baseName, carriesToServer, directionText, editsFile, isOpenedPdf, libraryWorkspaces, notesLocked,
  openedHere, readVault, vaultBanner, vaultChip, vaultConflictsPath,
} from "../src/vault/vaultState.js";

const folder = (over = {}) => ({ vault: { id: "v1", name: "Papers", scope: "folder", path: "D:\\Papers\\Attention.md", pull: true,
  push: "everything", problem: "", conflicts: 0, paused: false, ...over }, reader: null });
const opened = (over = {}) => ({ vault: null, reader: { path: "C:\\Downloads\\paper.pdf", missing: false, ...over } });

test("an answer with neither a vault nor a reader is no state", () => {
  assert.equal(readVault({ vault: null, reader: null }), null);
  assert.equal(readVault(null), null);
  assert.equal(readVault({ vault: "nonsense" }), null);
  assert.deepEqual(readVault(folder()), folder());
});

test("the last name of a path, either separator", () => {
  assert.equal(baseName("D:\\Papers\\Attention.md"), "Attention.md");
  assert.equal(baseName("/home/a/notes/x.md"), "x.md");
  assert.equal(baseName("/home/a/dir/"), "dir");
  assert.equal(baseName(""), "");
});

test("a folder vault's page says where it is synced, and what stands in its way", () => {
  const chip = vaultChip(folder());
  assert.equal(chip.label, "Synced: Attention.md");
  assert.match(chip.title, /synced with D:\\Papers\\Attention\.md/);
  const busy = vaultChip(folder({ paused: true, conflicts: 2, problem: "an open code block" }));
  assert.ok(busy.problem && busy.paused);
  assert.match(busy.title, /paused/);
  assert.match(busy.title, /2 conflicts/);
  assert.match(busy.title, /Not written to disk: an open code block/);
});

test("a member who cannot see the path reads the vault's name", () => {
  assert.equal(vaultChip(folder({ path: "" })).label, "Synced: Papers");
});

test("a vault of one file says where its PDF is pushed from", () => {
  const chip = vaultChip({ vault: { id: "f", name: "main.pdf", scope: "file", path: "D:\\tex\\main.pdf", pull: false, push: "pdfs" }, reader: null });
  assert.equal(chip.label, "Pushed from: main.pdf");
  assert.match(chip.title, /pushed from D:\\tex\\main\.pdf/);
});

test("an opened PDF is on this computer, or its file is missing", () => {
  assert.equal(vaultChip(opened()).label, "On this computer");
  assert.match(vaultChip(opened()).title, /C:\\Downloads\\paper\.pdf/);
  const gone = vaultChip(opened({ missing: true }));
  assert.equal(gone.label, "File missing");
  assert.ok(gone.missing);
  assert.equal(vaultChip(null), null);
});

test("the banner: a note not written, else a file that is gone", () => {
  assert.equal(vaultBanner(folder()), null);
  assert.deepEqual(vaultBanner(folder({ problem: "an open code block" })), { kind: "problem", reason: "an open code block" });
  assert.deepEqual(vaultBanner(opened({ missing: true })), { kind: "missing", path: "C:\\Downloads\\paper.pdf" });
  assert.equal(vaultBanner(opened()), null);
  assert.equal(vaultBanner(null), null);
});

test("the notes lock: a folder vault that does not pull and pushes everything, never a file vault or an opened PDF", () => {
  assert.equal(notesLocked(folder()), false);
  assert.equal(notesLocked(folder({ pull: false })), true);
  assert.equal(notesLocked(folder({ pull: false, push: "pdfs" })), false, "a notes file is never pushed: the notes are Gamma's");
  assert.equal(notesLocked({ vault: { scope: "file", pull: false, push: "pdfs" }, reader: null }), false);
  assert.equal(notesLocked(opened()), false);
  assert.equal(notesLocked(null), false);
});

test("the notes file is edited here only where the vault pushes everything and the page has one", () => {
  assert.equal(editsFile(folder()), true);
  assert.equal(editsFile(folder({ push: "pdfs" })), false);
  assert.equal(editsFile(folder({ path: "D:\\Papers\\paper.pdf" })), false);
  assert.equal(editsFile(folder({ path: "" })), false);
  assert.equal(editsFile(opened()), false);
});

test("Add to library… is an opened PDF's, and another server's library one whose file is there", () => {
  assert.equal(addsToLibrary(opened()), true);
  assert.equal(addsToLibrary(folder()), false);
  assert.equal(addsToLibrary(null), false);
  assert.equal(carriesToServer(opened()), true);
  assert.equal(carriesToServer(opened({ missing: true })), false, "a file gone has nothing to carry");
  assert.equal(carriesToServer(opened({ mine: false })), false, "another account's opened file is theirs to carry");
  assert.equal(carriesToServer(opened({ mine: true })), true);
  assert.equal(carriesToServer(folder()), false);
  assert.equal(carriesToServer(null), false);
});

test("an opened file is this account's unless the server says another account opened it", () => {
  assert.equal(openedHere(opened({ mine: true })), true);
  assert.equal(openedHere(opened({ mine: false })), false);
  assert.equal(openedHere(opened()), true, "an answer without the field: the server and the app still check");
  assert.equal(openedHere(null), true);
});

test("Add to library… offers the workspace open here, then the others this account edits that are no clone", () => {
  const list = [
    { id: "lab", name: "Lab", role: "viewer" },
    { id: "me", name: "Mine", role: "owner", personal: true },
    { id: "team", name: "Team", role: "editor" },
    { id: "copy", name: "Copy", role: "owner", mirror_of: "https://nas/lab" },
    { id: "read", name: "Reading", role: "viewer" },
  ];
  assert.deepEqual(libraryWorkspaces(list, "team"), [{ id: "team", name: "Team" }, { id: "me", name: "Mine" }]);
  assert.deepEqual(libraryWorkspaces(list, "me").map((w) => w.id), ["me", "team"]);
  assert.deepEqual(libraryWorkspaces([], "me"), []);
  assert.deepEqual(libraryWorkspaces(undefined, "me"), []);
});

test("a folder vault's conflicts are under its link", () => {
  assert.equal(vaultConflictsPath(folder()), "/folder-links/v1");
  assert.equal(vaultConflictsPath({ vault: { id: "f", scope: "file" } }), "");
  assert.equal(vaultConflictsPath(opened()), "");
});

test("the direction in the switches' words", () => {
  assert.equal(directionText({ pull: true, push: "everything" }), "Pull: on · Push: everything");
  assert.equal(directionText({ pull: false, push: "pdfs" }), "Pull: off · Push: PDFs");
  assert.equal(directionText({ pull: true, push: "off" }), "Pull: on · Push: off");
});

test("an opened PDF is a linked page inside Opened files", () => {
  const rows = new Map([
    ["opened", { _opened: true }],
    ["paper", { _linkedFile: true, _parentPage: "opened" }],
    ["vaultPaper", { _linkedFile: true }],
    ["inside", { _linkedFile: true, _parentPage: "other" }],
    ["other", {}],
    ["plain", { _parentPage: "opened" }],
  ]);
  assert.equal(isOpenedPdf("paper", rows), true);
  assert.equal(isOpenedPdf("vaultPaper", rows), false);
  assert.equal(isOpenedPdf("inside", rows), false);
  assert.equal(isOpenedPdf("plain", rows), false);
  assert.equal(isOpenedPdf("nowhere", rows), false);
});

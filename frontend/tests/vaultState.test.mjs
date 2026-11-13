// The open page's standing with this computer (src/vault/vaultState.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addsToLibrary, agentLine, bannerKey, baseName, carriesToServer, directionText, editsFile, isOpenedPdf, libraryWorkspaces,
  mentionAgent, notesLocked, openedHere, readVault, vaultBanner, vaultChip, vaultConflictsPath,
} from "../src/vault/vaultState.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

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
  assert.equal(chip.kind, "sync");
  assert.match(chip.title, /synced with D:\\Papers\\Attention\.md/);
  assert.equal(vaultChip(folder({ paused: true })).kind, "paused");
  const busy = vaultChip(folder({ paused: true, conflicts: 2, problem: "an open code block" }));
  assert.equal(busy.kind, "alert", "a problem comes before being paused");
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
  assert.equal(chip.kind, "push");
  assert.match(chip.title, /pushed from D:\\tex\\main\.pdf/);
});

test("an opened PDF is on this computer, or its file is missing", () => {
  assert.equal(vaultChip(opened()).label, "On this computer");
  assert.equal(vaultChip(opened()).kind, "disk");
  assert.match(vaultChip(opened()).title, /C:\\Downloads\\paper\.pdf/);
  const gone = vaultChip(opened({ missing: true }));
  assert.equal(gone.label, "File missing");
  assert.equal(gone.kind, "alert");
  assert.equal(vaultChip(null), null);
});

test("the banner: a note not written, else a file that is gone", () => {
  assert.equal(vaultBanner(folder()), null);
  assert.deepEqual(vaultBanner(folder({ problem: "an open code block" })), { kind: "problem", reason: "an open code block" });
  assert.deepEqual(vaultBanner(opened({ missing: true })), { kind: "missing", path: "C:\\Downloads\\paper.pdf" });
  assert.equal(vaultBanner(opened()), null);
  assert.equal(vaultBanner(null), null);
});

test("a dismissed banner is remembered by its page and what it says", () => {
  const problem = (reason) => vaultBanner(folder({ problem: reason }));
  assert.equal(bannerKey("p1", problem("an open code block")), bannerKey("p1", problem("an open code block")));
  assert.notEqual(bannerKey("p1", problem("an open code block")), bannerKey("p1", problem("a table")), "another reason shows again");
  assert.notEqual(bannerKey("p1", problem("a table")), bannerKey("p2", problem("a table")), "another page's is its own");
  assert.notEqual(bannerKey("p1", vaultBanner(opened({ missing: true }))), bannerKey("p1", problem("C:\\Downloads\\paper.pdf")));
  assert.equal(bannerKey("p1", null), "");
});

test("the notes lock: a folder vault that does not pull and pushes everything, never a file vault or an opened PDF", () => {
  assert.equal(notesLocked(folder()), false);
  assert.equal(notesLocked(folder({ pull: false })), true);
  assert.equal(notesLocked(folder({ pull: false, push: "pdfs" })), false, "a notes file is never pushed: the notes are Gamma's");
  assert.equal(notesLocked({ vault: { scope: "file", pull: false, push: "pdfs" }, reader: null }), false);
  assert.equal(notesLocked(opened()), false);
  assert.equal(notesLocked(null), false);
  // the page's document: a work folder's picture or text file has no notes file, so its notes are Gamma's
  const locked = folder({ pull: false });
  assert.equal(notesLocked(locked, { format: "pdf", at: "path" }), true);
  assert.equal(notesLocked(locked, null), true, "a note page");
  assert.equal(notesLocked(locked, { format: "image", at: "path" }), false);
  assert.equal(notesLocked(locked, { format: "text", at: "path" }), false);
  assert.equal(notesLocked(locked, { format: "binary", at: "listed" }), false);
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

test("an opened PDF is a page read in place inside Opened files", () => {
  const here = { _doc: { format: "pdf", at: "path" } };
  const rows = new Map([
    ["opened", { _opened: true }],
    ["paper", { ...here, _parentPage: "opened" }],
    ["vaultPaper", here],
    ["inside", { ...here, _parentPage: "other" }],
    ["other", {}],
    ["plain", { _parentPage: "opened" }],
    ["stored", { _doc: { format: "pdf", at: "store" }, _parentPage: "opened" }],
  ]);
  assert.equal(isOpenedPdf("paper", rows), true);
  assert.equal(isOpenedPdf("vaultPaper", rows), false);
  assert.equal(isOpenedPdf("inside", rows), false);
  assert.equal(isOpenedPdf("plain", rows), false);
  assert.equal(isOpenedPdf("stored", rows), false);
  assert.equal(isOpenedPdf("nowhere", rows), false);
});

test("App's page list (libraryUtils.pageBlocksOf) carries what isOpenedPdf reads", () => {
  const doc = (at) => ({ format: "pdf", name: "paper.pdf", hash: "a1b2c3", ext: ".pdf", at });
  const listed = [
    { id: "opened", content: "Opened files", properties: { opened_files: true } },
    { id: "paper", content: "paper.pdf", _parentPage: "opened", properties: { doc: doc("path") } },
    { id: "stored", content: "stored.pdf", _parentPage: "opened", properties: { doc: doc("store") } },
    { id: "notes", content: "Notes", _parentPage: "opened", properties: {} },
  ];
  const rows = new Map(pageBlocksOf(listed, libraryTree({})).map((b) => [b.id, b]));
  assert.equal(rows.get("opened")._opened, true);
  assert.equal(rows.get("paper")._doc.at, "path");
  assert.equal(isOpenedPdf("paper", rows), true);
  assert.equal(isOpenedPdf("stored", rows), false, "a page there whose PDF is stored");
  assert.equal(isOpenedPdf("notes", rows), false, "a page there that reads no file of its own");
});

test("a work folder's agent on this computer: named, its problem while it cannot run, what it is at while it works", () => {
  const agent = (extra) => folder({ agent: { name: "codex", label: "Codex", ready: true, problem: "", running: null, ...extra } });
  assert.deepEqual(agentLine(agent()), { line: "Agent: Codex on this computer", problem: "", working: "" });
  assert.equal(agentLine(agent({ ready: false, problem: "Codex is not on this computer." })).problem, "Codex is not on this computer.");
  assert.equal(agentLine(agent({ ready: null, problem: "" })).problem, "", "still checking");
  assert.equal(agentLine(agent({ running: { request_id: "r1", detail: "Editing main.tex" } })).working, "Codex is working: Editing main.tex");
  assert.equal(agentLine(agent({ running: { request_id: "r1", detail: "" } })).working, "Codex is working", "a member who cannot act on it");
  assert.equal(agentLine(folder({ agent: null })), null);
  assert.equal(agentLine(folder()), null);
  assert.equal(agentLine(opened()), null);
  assert.equal(agentLine(null), null);
});

test("the agent a comment names: the work folder's, for its maker alone", () => {
  const codex = { name: "codex", label: "Codex", ready: true, problem: "", running: null };
  const work = (over = {}, agent = {}) => folder({ work_folder: true, mine: true, agent: { ...codex, ...agent }, ...over });
  assert.deepEqual(mentionAgent(work()), { name: "codex", label: "Codex", ready: true, problem: "" });
  assert.deepEqual(mentionAgent(work({}, { running: { request_id: "r1", detail: "Editing main.tex" } })),
    { name: "codex", label: "Codex", ready: true, problem: "" }, "working: still named");
  assert.equal(mentionAgent(work({}, { ready: false, problem: "Codex is not signed in." })).problem, "Codex is not signed in.");
  assert.equal(mentionAgent(work({}, { ready: null, problem: "stale" })).problem, "", "still checking");
  assert.equal(mentionAgent(work({ mine: false })), null, "a member who did not make the vault");
  assert.equal(mentionAgent(work({ work_folder: false })), null);
  assert.equal(mentionAgent(work({ agent: null })), null, "no agent on");
  assert.equal(mentionAgent(opened()), null);
  assert.equal(mentionAgent(null), null);
});

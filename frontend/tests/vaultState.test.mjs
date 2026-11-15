// The open page's standing with this computer (src/vault/vaultState.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addsToLibrary, agentLine, agentWorking, bannerKey, baseName, carriesToServer, computerChip, directionText, editsFile, folderKeeper, isOpenedFile,
  libraryWorkspaces, notesLocked, openedHere, openedNote, opensAsPdf, readComputers, readVault, showsOnDisk, sinceText, vaultBanner,
  vaultChip, vaultConflictsPath,
} from "../src/vault/vaultState.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

const folder = (over = {}) => ({ vault: { id: "v1", name: "Papers", scope: "folder", path: "D:\\Papers\\Attention.md", pull: true,
  push: "everything", problem: "", conflicts: 0, paused: false, ...over }, reader: null });
const opened = (over = {}) => ({ vault: null, reader: { path: "C:\\Downloads\\paper.pdf", missing: false, format: "pdf", ext: ".pdf", ...over } });
// an opened file of another format: a LaTeX source, a lone Markdown file read as text
const openedText = (over = {}) => opened({ path: "C:\\Papers\\main.tex", format: "text", ext: ".tex", ...over });

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

test("Show in folder: a vault's or an opened file's path, while its file is there", () => {
  assert.equal(showsOnDisk(folder()), true);
  assert.equal(showsOnDisk(opened()), true);
  assert.equal(showsOnDisk(opened({ missing: true })), false, "a missing file");
  assert.equal(showsOnDisk(folder({ path: "" })), false, "a member who cannot see the path");
  assert.equal(showsOnDisk(null), false);
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
  assert.equal(gone.title, "The file this PDF was read from is gone");
  assert.equal(vaultChip(opened({ path: "" })).title, "This PDF is read from a file on this computer, not stored in the library");
  assert.equal(vaultChip(null), null);
});

test("an opened file of another format says so in a file's words, not a PDF's", () => {
  const chip = vaultChip(openedText());
  assert.equal(chip.label, "On this computer");
  assert.equal(chip.kind, "disk");
  assert.equal(chip.title, "This file is read from C:\\Papers\\main.tex, not stored in the library");
  assert.equal(vaultChip(openedText({ path: "" })).title, "This file is read where it lies on this computer, not stored in the library");
  const gone = vaultChip(openedText({ missing: true }));
  assert.deepEqual([gone.label, gone.kind, gone.title], ["File missing", "alert", "The file this page was read from is gone"]);
  assert.equal(vaultChip(openedText({ format: "image", ext: ".png" })).title.startsWith("This file is read from"), true, "a picture too");
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
  assert.equal(addsToLibrary(openedText()), true, "an opened file of any format is added to the library");
  assert.equal(carriesToServer(openedText()), false, "only a PDF is carried to another server");
  assert.equal(carriesToServer(openedText({ format: "image", ext: ".png" })), false);
});

test("what an opened file is: a PDF, or a Markdown file read as text that never joins the library", () => {
  assert.equal(opensAsPdf({ format: "pdf", ext: ".pdf" }), true);
  assert.equal(opensAsPdf(opened().reader), true, "the open page's reader carries its document's format");
  assert.equal(opensAsPdf({ format: "text", ext: ".tex" }), false);
  assert.equal(opensAsPdf({ format: "image", ext: ".png" }), false);
  assert.equal(opensAsPdf(null), false);
  assert.equal(openedNote({ format: "text", ext: ".md" }), true);
  assert.equal(openedNote({ format: "text", ext: ".markdown" }), true);
  assert.equal(openedNote({ format: "text", ext: ".MD" }), true);
  assert.equal(openedNote(openedText({ ext: ".md" }).reader), true);
  assert.equal(openedNote({ format: "text", ext: ".tex" }), false, "a LaTeX source joins the library");
  assert.equal(openedNote({ format: "text", ext: ".txt" }), false);
  assert.equal(openedNote({ format: "markdown", ext: ".md" }), false, "a note is no text document");
  assert.equal(openedNote({ format: "pdf", ext: ".pdf" }), false);
  assert.equal(openedNote(null), false);
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

test("an opened file is a page read in place inside Opened files, of any format", () => {
  const here = { _doc: { format: "pdf", at: "path" } };
  const rows = new Map([
    ["opened", { _opened: true }],
    ["paper", { ...here, _parentPage: "opened" }],
    ["vaultPaper", here],
    ["inside", { ...here, _parentPage: "other" }],
    ["other", {}],
    ["plain", { _parentPage: "opened" }],
    ["stored", { _doc: { format: "pdf", at: "store" }, _parentPage: "opened" }],
    ["source", { _doc: { format: "text", ext: ".tex", at: "path" }, _parentPage: "opened" }],
  ]);
  assert.equal(isOpenedFile("paper", rows), true);
  assert.equal(isOpenedFile("vaultPaper", rows), false);
  assert.equal(isOpenedFile("inside", rows), false);
  assert.equal(isOpenedFile("plain", rows), false);
  assert.equal(isOpenedFile("stored", rows), false);
  assert.equal(isOpenedFile("nowhere", rows), false);
  assert.equal(isOpenedFile("source", rows), true, "a LaTeX source read where it lies");
});

test("App's page list (libraryUtils.pageBlocksOf) carries what isOpenedFile reads", () => {
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
  assert.equal(isOpenedFile("paper", rows), true);
  assert.equal(isOpenedFile("stored", rows), false, "a page there whose PDF is stored");
  assert.equal(isOpenedFile("notes", rows), false, "a page there that reads no file of its own");
});

test("a work folder's agent on this computer: named, its problem while it cannot run, what it is at while it works", () => {
  const agent = (extra) => folder({ agent: { name: "codex", label: "Codex", ready: true, problem: "", running: null, ...extra } });
  assert.deepEqual(agentLine(agent()), { line: "Agent: Codex on this computer", problem: "", working: null });
  assert.equal(agentLine(agent({ ready: false, problem: "Codex is not on this computer." })).problem, "Codex is not on this computer.");
  assert.equal(agentLine(agent({ ready: null, problem: "" })).problem, "", "still checking");
  assert.deepEqual(agentLine(agent({ running: { detail: "Editing main.tex" } })).working,
    { label: "Codex is working…", detail: "Editing main.tex", title: "Editing main.tex", tone: "pending", live: true });
  assert.deepEqual(agentLine(agent({ running: { detail: "" } })).working,
    { label: "Codex is working…", detail: "", title: "Codex is working…", tone: "pending", live: true }, "a member who cannot act on it");
  assert.equal(agentWorking("Codex", "Editing main.tex"), "Codex is working: Editing main.tex");
  assert.equal(agentWorking("Codex"), "Codex is working…");
  assert.equal(agentWorking(""), "The agent is working…");
  assert.equal(agentLine(folder({ agent: null })), null);
  assert.equal(agentLine(folder()), null);
  assert.equal(agentLine(opened()), null);
  assert.equal(agentLine(null), null);
});

const codexThere = { name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "" };
const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null, vault: { kind: "folder", name: "cavity", paused: false },
  assistants: [codexThere], ...over });

test("the page's computers: kept only when the answer names them", () => {
  assert.deepEqual(readComputers({ computers: [pc()] }), [pc()]);
  assert.deepEqual(readComputers({ computers: [null, "x", { id: "c2" }, pc()] }), [pc()], "a computer with no name is none");
  assert.deepEqual(readComputers({ computers: "nonsense" }), []);
  assert.deepEqual(readComputers(null), []);
});

test("when a computer went offline: the time today, the day this year, the year before", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  assert.match(sinceText(new Date(2026, 10, 13, 14, 2).toISOString(), now), /2:02/);
  const day = sinceText(new Date(2026, 10, 3, 9, 0).toISOString(), now);
  assert.match(day, /Nov/);
  assert.match(day, /3/);
  assert.doesNotMatch(day, /2026|9:00/);
  assert.match(sinceText(new Date(2025, 10, 3).toISOString(), now), /2025/);
  assert.equal(sinceText(null, now), "");
  assert.equal(sinceText("nonsense", now), "");
});

test("the computer's chip: its name, connected or offline since, the vault it keeps, and the assistants there", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  const chip = computerChip([pc()], now);
  assert.equal(chip.label, "Synced folder on TIM-PC");
  assert.equal(chip.online, true);
  assert.equal(chip.state, "Connected");
  assert.equal(chip.vault, "Folder: cavity");
  assert.equal(chip.paused, "");
  assert.equal(chip.assistants, "Assistants there: Codex");
  assert.equal(chip.title, "Connected\nFolder: cavity\nAssistants there: Codex");
  const away = computerChip([pc({ online: false, since: new Date(2026, 10, 13, 14, 2).toISOString() })], now);
  assert.equal(away.online, false);
  assert.match(away.state, /^Offline since 2:02/);
  assert.equal(computerChip([pc({ online: false })], now).state, "Offline", "no time known");
  assert.equal(computerChip([pc({ vault: { kind: "file", name: "main.pdf", paused: true } })], now).vault, "File: main.pdf");
  assert.equal(computerChip([pc({ vault: { kind: "file", name: "main.pdf" } })], now).label, "Synced vault on TIM-PC", "a vault of one file");
  assert.equal(computerChip([pc({ vault: { kind: "file", name: "main.pdf", paused: true } })], now).paused, "Paused on TIM-PC");
  assert.equal(computerChip([pc({ online: false }), pc({ id: "c2", name: "LAPTOP" })], now).label, "Synced folder on TIM-PC", "the first: the server sends online ones first");
  assert.equal(computerChip([], now), null);
  assert.equal(computerChip(null, now), null);
});

test("the assistants there: by their code, never the computer's own words; none found, no line", () => {
  const claude = { name: "claude", label: "Claude Code", found: true, ready: true, version: "2.1.318", code: "" };
  const line = (assistants) => computerChip([pc({ assistants })]).assistants;
  assert.equal(line([codexThere, claude]), "Assistants there: Codex, Claude Code");
  assert.equal(line([{ ...codexThere, ready: false, code: "signed_out" }, { ...claude, ready: null, code: "checking" }]),
    "Assistants there: Codex (not signed in), Claude Code (checking)");
  assert.equal(line([{ ...codexThere, ready: false, code: "not_ready" }]), "Assistants there: Codex (not ready)");
  assert.equal(line([{ name: "codex", label: "Codex", found: null, ready: null, version: "", code: "checking" }]),
    "Assistants there: Codex (checking)", "not yet found or not: the computer is still checking");
  assert.equal(line([{ name: "claude", found: true, ready: true, code: "" }]), "Assistants there: Claude Code", "no label: its name");
  assert.equal(line([{ ...codexThere, found: false, ready: false, code: "missing" }, claude]), "Assistants there: Claude Code");
  assert.equal(line([{ ...codexThere, found: false, ready: false, code: "missing" }]), "");
  assert.equal(line([]), "");
  assert.equal(line(undefined), "");
});

test("a synced folder's root names the computer that keeps it and how its sync stands", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  const link = (status = {}, more = {}) => ({ id: "v1", status, choosing: false, ...more });
  assert.deepEqual(folderKeeper(link({ last_sync: "x" }), null, now), { text: "This computer · Synced", title: "Kept on This computer\nSynced" });
  assert.equal(folderKeeper(link({ paused_at: "x" }), null, now).text, "This computer · Paused");
  assert.equal(folderKeeper(link({ stopped: "gone" }), null, now).text, "This computer · Not syncing");
  assert.equal(folderKeeper(link({ running: true }), null, now).text, "This computer · Syncing…");
  assert.equal(folderKeeper(link({}, { choosing: true }), null, now).text, "This computer · Choosing files…");
  const pc = { name: "TIM-PC", online: true, since: null, paused: false };
  assert.equal(folderKeeper(link(), pc, now).text, "This computer · Synced", "this desktop app's own vault first");
  assert.equal(folderKeeper(null, pc, now).text, "TIM-PC · Connected");
  assert.equal(folderKeeper(null, { ...pc, paused: true }, now).text, "TIM-PC · Paused");
  assert.match(folderKeeper(null, { ...pc, online: false, since: new Date(2026, 10, 13, 14, 2).toISOString() }, now).text, /^TIM-PC · Offline since 2:02/);
  assert.equal(folderKeeper(null, { ...pc, online: false }, now).text, "TIM-PC · Offline");
  assert.equal(folderKeeper(null, null, now), null, "nobody this account knows keeps it");
});

test("the computer's chip names the first computer that keeps the page, not one whose agents alone it offers", () => {
  const desk = pc({ id: "c0", name: "DESK", vault: null });
  const chip = computerChip([desk, pc({ vault: { kind: "folder", name: "cavity", paused: false, work: true } })]);
  assert.equal(chip.label, "Synced folder on TIM-PC");
  assert.equal(chip.title, "Connected\nFolder: cavity\nAssistants there: Codex");
  assert.equal(computerChip([desk]), null, "none keeps it");
});

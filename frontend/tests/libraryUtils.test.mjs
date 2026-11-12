// Pure library rules: the folder and label trees of the listing, then the
// dates, the pages' documents and links.
import assert from "node:assert/strict";
import { test } from "node:test";
import { generateKeyBetween } from "fractional-indexing";
import {
  NO_LABEL, NO_LABEL_TITLE, addToFolder, childFolders, defaultPageTitle, filedIn, findNamed, findPageForUrl,
  firstFolderPath, folderAtPath, folderChain, folderCompletion, folderEntries, folderInside, folderMatches,
  folderNeighbour, folderPath, folderPathHolds, folderPathNames, folderPosition, folderPositions, folderSubtree, folderTargets,
  formatFullDate, formatLabel, formatRelativeTime, formatShortDate,
  inFolder, isFreshLibrary, isLinkInput, labelName, libraryTree, normalizeLinkInput, pageBlocksOf, pageFormat, pageKindLabel,
  siblingFolders,
} from "../src/library/libraryUtils.js";

// The listing's trees: physics (with a subfolder whose name holds the path
// separator and a comma), then biology; two labels.
const [k1, k2] = [generateKeyBetween(null, null), generateKeyBetween(generateKeyBetween(null, null), null)];
const listing = {
  folders: { seq: 4, children: [
    { id: "phys", content: "Physics", position: k1, properties: { pinned: "2026-09-01T00:00:00Z" }, children: [
      { id: "qec", content: "QEC / codes, 2026", position: k1, properties: {}, created_at: "2026-10-01T09:00:00Z", children: [] },
    ] },
    { id: "bio", content: "Biology", position: k2, properties: {}, children: [] },
  ] },
  labels: { seq: 2, children: [
    { id: "llm", content: "llm", position: k1, properties: {}, children: [] },
    { id: "rev", content: "Review", position: k2, properties: {}, children: [] },
  ] },
};

test("libraryTree reads the folders by nesting and order, the labels flat", () => {
  const tree = libraryTree(listing);
  assert.deepEqual(tree.top, ["phys", "bio"]);
  assert.deepEqual(childFolders(tree, ""), ["phys", "bio"]);
  assert.deepEqual(childFolders(tree, "phys"), ["qec"]);
  assert.deepEqual(childFolders(tree, "gone"), []);
  assert.deepEqual(siblingFolders(tree, "").map((f) => f.name), ["Physics", "Biology"]);
  assert.deepEqual(siblingFolders(tree, "phys"), [tree.folders.get("qec")]);
  assert.deepEqual(siblingFolders(tree, "gone"), []);
  assert.deepEqual(tree.folders.get("qec"), { id: "qec", name: "QEC / codes, 2026", parent: "phys", position: k1,
    pinned: "", created: "2026-10-01T09:00:00Z", mark: null, path: ["Physics", "QEC / codes, 2026"], children: [] });
  assert.equal(tree.folders.get("phys").pinned, "2026-09-01T00:00:00Z");
  assert.equal(tree.folders.get("bio").created, "", "no created_at: no date of its own");
  assert.deepEqual([...tree.labels.values()].map((l) => l.name), ["llm", "Review"]);
  assert.equal(libraryTree().folders.size, 0, "no listing yet: empty trees");
});

test("libraryTree reads a work folder's mark off its folder block, as booleans", () => {
  const tree = libraryTree({ folders: { children: [
    { id: "w", content: "paper", properties: { vault: { pull: true, library: 1 } }, children: [
      { id: "d", content: "figures", properties: {}, children: [] },
    ] },
    { id: "odd", content: "odd", properties: { vault: [true] }, children: [] },
  ] } });
  assert.deepEqual(tree.folders.get("w").mark, { pull: true, library: false }, "only true is on");
  assert.equal(tree.folders.get("d").mark, null, "only the root carries it");
  assert.equal(tree.folders.get("odd").mark, null, "a mark that is no object is none");
});

test("paths read as names joined, never split; labels by id, No label by its title", () => {
  const tree = libraryTree(listing);
  assert.equal(folderPath(tree, "qec"), "Physics / QEC / codes, 2026");
  assert.equal(folderPath(tree, "gone"), "");
  assert.deepEqual(folderEntries(tree), [{ id: "phys", name: "Physics" }, { id: "qec", name: "Physics / QEC / codes, 2026" }, { id: "bio", name: "Biology" }]);
  assert.deepEqual(folderChain(tree, "qec"), ["phys", "qec"]);
  assert.deepEqual(folderChain(tree, "gone"), []);
  assert.ok(inFolder(tree, "qec", "phys") && inFolder(tree, "phys", "phys"));
  assert.ok(!inFolder(tree, "phys", "qec") && !inFolder(tree, "bio", "phys"));
  assert.deepEqual([...folderSubtree(tree, "phys")], ["phys", "qec"]);
  assert.deepEqual([...folderSubtree(tree, "gone")], []);
  assert.equal(labelName(tree, "rev"), "Review");
  assert.equal(labelName(tree, NO_LABEL), NO_LABEL_TITLE);
  assert.equal(labelName(tree, "gone"), "");
});

test("a folder moves into any folder but itself and those below it; it steps past its neighbours", () => {
  const tree = libraryTree(listing);
  assert.deepEqual(folderTargets(tree, ["phys"]), ["bio"], "not into itself or its subfolder");
  assert.deepEqual(folderTargets(tree, ["qec"]), ["phys", "bio"]);
  assert.deepEqual(folderTargets(tree, ["qec", "bio"]), ["phys"], "several: none of them");
  assert.deepEqual(folderTargets(tree, ["qec"], ["bio", "qec", "phys"]), ["bio", "phys"], "in the order given");
  assert.equal(folderNeighbour(tree, "bio", -1), "phys");
  assert.equal(folderNeighbour(tree, "phys", 1), "bio");
  assert.equal(folderNeighbour(tree, "phys", -1), "", "already first");
  assert.equal(folderNeighbour(tree, "qec", 1), "", "an only child");
  assert.equal(folderNeighbour(tree, "gone", 1), "");
});

test("a filing keeps the ids that name a block; filing into a folder refines the one above away", () => {
  const tree = libraryTree(listing);
  assert.deepEqual(filedIn(tree.folders, ["bio", "gone", "phys"]), ["bio", "phys"], "a dangling id is passed by");
  assert.deepEqual(filedIn(tree.labels, undefined), []);
  assert.equal(firstFolderPath(tree, ["gone", "qec", "bio"]), "Physics / QEC / codes, 2026", "the first that names a block");
  assert.equal(firstFolderPath(tree, ["gone"]), "");
  assert.equal(firstFolderPath(tree, undefined), "");
  assert.deepEqual(addToFolder(tree, ["phys", "bio"], "qec"), ["bio", "qec"], "physics gives way to its subfolder");
  assert.deepEqual(addToFolder(tree, ["gone"], "bio"), ["bio"], "the refiling drops a dangling id");
  assert.deepEqual(addToFolder(tree, [], "phys"), ["phys"]);
});

// A deeper tree for the typed paths below: Lab holds Clocks (holding
// Lattice) and Ions, and two top folders are named alike but for their case.
const deep = libraryTree({ folders: { children: [
  { id: "lab", content: "Lab", position: k1, children: [
    { id: "clocks", content: "Clocks", position: k1, children: [
      { id: "lattice", content: "Lattice", position: k1, children: [] },
    ] },
    { id: "ions", content: "Ions", position: k2, children: [] },
  ] },
  { id: "slash", content: "a/b", position: k2, children: [] },
] } });

test("a typed folder path splits on \"/\", and a path holds a typed one name by name", () => {
  assert.deepEqual(folderPathNames(" phys / c "), ["phys", "c"]);
  assert.deepEqual(folderPathNames("2026"), ["2026"]);
  assert.deepEqual(folderPathNames(" / / "), [], "blank names are dropped");
  assert.ok(folderPathHolds(["Physics", "Clocks"], "phys/c"), "each name within consecutive names of the path");
  assert.ok(folderPathHolds(["Physics", "Clocks"], "clo"), "one name, anywhere in the path");
  assert.ok(folderPathHolds(["Physics", "Clocks"], "Physics / Clo"), "the separator as it reads");
  assert.ok(!folderPathHolds(["Physics", "Clocks"], "c/phys"), "in order, not just present");
  assert.ok(!folderPathHolds(["Physics"], "phys/c"), "more names than the path has");
  assert.ok(folderPathHolds(["a/b"], "a/b"), "a name holding the separator is found by typing it whole");
});

test("folderAtPath takes the path as it reads first, else walks it name by name", () => {
  assert.equal(folderAtPath(deep, "lab/clocks").id, "clocks", "ignoring case, like the server's named");
  assert.equal(folderAtPath(deep, " Lab / Clocks / Lattice ").id, "lattice");
  assert.equal(folderAtPath(deep, "Clocks"), null, "a path starts at the top");
  assert.equal(folderAtPath(deep, "Lab/Gone"), null);
  assert.equal(folderAtPath(deep, ""), null);
  assert.equal(folderAtPath(deep, "a/b").id, "slash", "the folder actually called that, not a walk");
  assert.equal(folderAtPath(libraryTree(listing), "Physics / QEC / codes, 2026").id, "qec");
});

test("a typed path ending in the separator opens that folder; Tab writes one out in full", () => {
  assert.equal(folderInside(deep, "Lab /").id, "lab");
  assert.equal(folderInside(deep, "lab/clocks/").id, "clocks");
  assert.equal(folderInside(deep, "Lab / Ions / "), null, "nothing inside it to open");
  assert.equal(folderInside(deep, "Lab"), null, "no separator: a name being typed");
  assert.equal(folderCompletion(deep, "lab"), "Lab / ", "subfolders: Tab leaves it open for the next name");
  assert.equal(folderCompletion(deep, "lattice"), "Lab / Clocks / Lattice");
  assert.equal(folderCompletion(deep, "gone"), "");
});

test("folderMatches lists what is inside an opened folder, else every path that holds the text", () => {
  const names = (text, skip) => folderMatches(deep, text, skip).map((f) => f.name);
  assert.deepEqual(names("Lab /"), ["Lab / Clocks", "Lab / Ions"], "what is right inside it, in its order");
  assert.deepEqual(names("Lab /", ["clocks"]), ["Lab / Ions"], "a folder already filed is left out");
  assert.deepEqual(names("lab/c"), ["Lab / Clocks", "Lab / Clocks / Lattice"]);
  assert.deepEqual(names("la"), ["Lab", "Lab / Clocks / Lattice", "Lab / Clocks", "Lab / Ions"],
    "the folders the typed name begins come before those merely holding it");
  assert.deepEqual(names("Gone"), []);
});

test("findNamed: exactly, else ignoring case", () => {
  const items = [{ id: "a", name: "Review" }, { id: "b", name: "review" }];
  assert.equal(findNamed(items, " review ").id, "b");
  assert.equal(findNamed(items, "REVIEW").id, "a");
  assert.equal(findNamed(items, "other"), null);
});

test("folderPosition orders by fractional keys: last, before a sibling, a sibling moved", () => {
  const tree = libraryTree(listing);
  assert.ok(folderPosition(tree, "") > k2, "a new top folder goes last");
  const first = folderPosition(tree, "", "phys");
  assert.ok(first < k1, "before the first");
  const between = folderPosition(tree, "", "bio", "phys");
  assert.ok(between < k2, "physics moved before biology stays before it");
  assert.ok(folderPosition(tree, "bio") > "", "into an empty folder");
});

test("folderPositions: several folders in order between the same two neighbours, the moved ones counted out", () => {
  const tree = libraryTree(listing);
  const [a, b] = folderPositions(tree, "", "bio", ["qec", "phys"]);
  assert.ok(a < b && b < k2, "both before biology, in order");
  assert.deepEqual(folderPositions(tree, "", "", ["qec"]).map((p) => p > k2), [true], "after the last");
  assert.deepEqual(folderPositions(tree, "", "bio", []), [], "nothing to place");
  assert.equal(folderPositions(tree, "", "bio", ["phys"])[0], folderPosition(tree, "", "bio", "phys"));
});

test("formatShortDate: today with the time, yesterday, a day this year, else the year", () => {
  // Built in local time: the column speaks the reader's time zone.
  const now = new Date(2026, 8, 13, 15, 0).getTime();
  const iso = (...parts) => new Date(...parts).toISOString();
  assert.match(formatShortDate(iso(2026, 8, 13, 9, 41), now), /^Today, 9:41/);
  assert.equal(formatShortDate(iso(2026, 8, 12, 23, 59), now), "Yesterday");
  assert.equal(formatShortDate(iso(2026, 8, 1, 8, 0), now), "Sep 1");
  assert.equal(formatShortDate(iso(2026, 0, 2, 8, 0), now), "Jan 2");
  assert.equal(formatShortDate(iso(2023, 5, 1), now), "2023");
  assert.match(formatShortDate(iso(2026, 8, 13, 16, 0), now), /^Today/, "a clock a little ahead is still today");
  assert.equal(formatShortDate("", now), "");
  assert.equal(formatShortDate("not a date", now), "");
  // A bare server timestamp is UTC, like formatRelativeTime's.
  assert.equal(formatShortDate("2023-06-01T12:00:00", now), "2023");
  assert.match(formatFullDate(iso(2026, 8, 12, 9, 5)), /Sep 12, 2026/);
});

test("formatRelativeTime picks the coarsest unit and reads bare timestamps as UTC", () => {
  const now = Date.parse("2026-09-13T12:00:00Z");
  assert.equal(formatRelativeTime("2026-09-13T11:59:30Z", now), "30s ago");
  assert.equal(formatRelativeTime("2026-09-13T11:15:00", now), "45m ago");
  assert.equal(formatRelativeTime("2026-09-13T09:00:00Z", now), "3h ago");
  assert.equal(formatRelativeTime("2026-09-10T12:00:00Z", now), "3d ago");
  assert.equal(formatRelativeTime("2026-09-06T12:00:00Z", now), "1w ago");
  assert.equal(formatRelativeTime("2026-07-01T12:00:00Z", now), "2mo ago");
  assert.equal(formatRelativeTime("2025-09-13T12:00:00Z", now), "1y ago");
  assert.equal(formatRelativeTime("2026-09-13T12:00:05Z", now), "1s ago", "the future clamps to a second");
  assert.equal(formatRelativeTime("", now), "");
});

test("findPageForUrl resolves a DOI or arXiv id against the library", () => {
  const at = (url, hash = "") => ({ doc: { format: "pdf", name: "", hash, ext: ".pdf", at: "url", url } });
  const pages = [
    { id: "p1", properties: { meta: { doi: "10.1000/ABC.1" } } },
    { id: "p2", properties: at("https://arxiv.org/pdf/2101.00001v2.pdf", "u1") },
    { id: "p3", properties: { meta: { arxiv_id: "2202.02222" } } },
    { id: "p4", properties: at("https://doi.org/10.2000/XYZ.9") },
    { id: "p5", properties: { doc: { format: "pdf", name: "2303.03333.pdf", hash: "d5", ext: ".pdf", at: "store" } } },
  ];
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1", pages), "p1");
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1.", pages), "p1", "trailing punctuation dropped");
  assert.equal(findPageForUrl("https://arxiv.org/abs/2101.00001", pages), "p2");
  assert.equal(findPageForUrl("arXiv:2202.02222", pages), "p3");
  assert.equal(findPageForUrl("https://doi.org/10.2000/xyz.9", pages), "p4", "a DOI in the address it is read from");
  assert.equal(findPageForUrl("https://arxiv.org/abs/2303.03333", pages), null, "a stored file's name is no address");
  assert.equal(findPageForUrl("https://example.com/paper.pdf", pages), null);
  assert.equal(findPageForUrl("", pages), null);
});

test("isFreshLibrary: no pages, or only seeded ones", () => {
  assert.equal(isFreshLibrary([]), true);
  assert.equal(isFreshLibrary([{ id: "w", properties: { seeded: "welcome" } }]), true, "the Welcome page alone");
  assert.equal(isFreshLibrary([{ id: "w", properties: { seeded: "welcome" } }, { id: "s", properties: { seeded: "sketchbook" } }]),
    true, "the Welcome page and the Sketchbook");
  assert.equal(isFreshLibrary([{ id: "w", properties: { seeded: "welcome" } }, { id: "p", properties: {} }]), false);
  assert.equal(isFreshLibrary([{ id: "p" }]), false, "a page without properties is the user's");
});

test("normalizeLinkInput turns identifiers into URLs", () => {
  assert.equal(normalizeLinkInput("https://x.test/a"), "https://x.test/a");
  assert.equal(normalizeLinkInput("2101.00001"), "https://arxiv.org/abs/2101.00001");
  assert.equal(normalizeLinkInput("arXiv:2101.00001v2"), "https://arxiv.org/abs/2101.00001v2");
  assert.equal(normalizeLinkInput("doi: 10.1/x"), "https://doi.org/10.1/x");
  assert.equal(normalizeLinkInput("10.1/x"), "https://doi.org/10.1/x");
  assert.equal(normalizeLinkInput("  "), "");
});

// The link dialog's field: a link it offers to link to, or a search of the pages.
test("isLinkInput tells a link from a search", () => {
  for (const link of ["https://x.test/a", "http://x.test", "2101.00001", "2101.00001v2", "arXiv:2101.00001",
    "arxiv: 2101.00001", "10.1103/PhysRevLett.1", "doi: 10.1103/abc", " 10.48550/arXiv.2101.00001 "]) {
    assert.equal(isLinkInput(link), true, link);
  }
  for (const search of ["", "attention", "Cavity readout", "2101", "10.1103", "https://", "arxiv attention"]) {
    assert.equal(isLinkInput(search), false, search);
  }
});

// What a page carries (documents/pageDocument.js), on its listing row.
test("pageBlocksOf gives each row its document; a card says PDF or Page by it", () => {
  const rows = [
    { id: "a", content: "", properties: { doc: { format: "pdf", name: "a.pdf", hash: "d1", ext: ".pdf", at: "path" } } },
    { id: "b", content: "Notes", properties: {} },
  ];
  const [a, b] = pageBlocksOf(rows, libraryTree());
  assert.deepEqual(a._doc, { format: "pdf", language: null, name: "a.pdf", hash: "d1", ext: ".pdf", size: null, at: "path",
    url: "/api/uploads/d1.pdf", file: "d1.pdf", copy: false });
  assert.equal(b._doc, null);
  assert.equal(pageKindLabel(a._doc), "PDF");
  assert.equal(pageKindLabel(b._doc), "Page");
  assert.equal(pageKindLabel({ format: "text" }), "Page", "only a PDF is named");
});

test("pageBlocksOf: a work folder's file, its name and size, and whether the library lists a page", () => {
  const rows = [
    { id: "f", content: "main.pdf", _parentPage: "", properties: { work_folder: "W1", file_name: "main.pdf", folders: ["phys"],
      doc: { format: "pdf", name: "main.pdf", hash: "p1", ext: ".pdf", at: "path", size: 2048 } } },
    { id: "added", content: "Attention", _parentPage: "", properties: { work_folder: "W1", file_name: "a.md", library: true } },
    { id: "lib", content: "Paper", _parentPage: "", properties: { folders: ["bio"], file_name: 5 } },
    { id: "sub", content: "Inside", _parentPage: "lib", properties: {} },
  ];
  const [f, added, lib, sub] = pageBlocksOf(rows, libraryTree(listing));
  assert.deepEqual([f._workFolder, f._library, f._fileName, f._size, f._listed], ["W1", false, "main.pdf", 2048, false]);
  assert.deepEqual([added._workFolder, added._library, added._fileName, added._size, added._listed], ["W1", true, "a.md", null, true]);
  assert.deepEqual([lib._workFolder, lib._library, lib._fileName, lib._listed], ["", false, "", true]);
  assert.deepEqual([sub._listed, sub._folders], [true, ["bio"]], "a sub-page by its top");
});

test("pageBlocksOf: a page whose top the client does not know keeps its parent, and no folder", () => {
  const rows = [
    { id: "mid", content: "Mid", _parentPage: "gone", properties: { folders: ["bio"] } },
    { id: "low", content: "Low", _parentPage: "mid", properties: {} },
    { id: "orphan", content: "Orphan", _parentPage: "gone", properties: { folders: ["phys"] } },
    { id: "x", content: "X", _parentPage: "y", properties: { folders: ["bio"] } },
    { id: "y", content: "Y", _parentPage: "x", properties: {} },
  ];
  const byId = new Map(pageBlocksOf(rows, libraryTree(listing)).map((b) => [b.id, b]));
  assert.deepEqual([byId.get("orphan")._parentPage, byId.get("orphan")._path, byId.get("orphan")._folders, byId.get("orphan")._listed],
    ["gone", "", [], true]);
  assert.deepEqual([byId.get("low")._path, byId.get("low")._folders, byId.get("low")._listed], ["Mid", [], true],
    "the chain up to what is known");
  assert.equal(byId.get("mid")._subPages, 1);
  assert.deepEqual(byId.get("x")._folders, ["bio"], "a loop means no parent, as on the server: its own folders");
});

test("a file's format as the Format column reads it", () => {
  assert.equal(formatLabel({ format: "pdf" }), "PDF");
  assert.equal(formatLabel({ format: "markdown", language: null }), "Markdown");
  assert.equal(formatLabel({ format: "text", language: "latex" }), "LaTeX");
  assert.equal(formatLabel({ format: "text", language: "cpp" }), "C++");
  assert.equal(formatLabel({ format: "text", language: "toString" }), "Text", "a language without a name of its own");
  assert.equal(formatLabel({ format: "image" }), "Picture");
  assert.equal(formatLabel({ format: "binary" }), "File");
  assert.equal(formatLabel({ format: "nonsense" }), "");
  assert.equal(formatLabel(null), "");
  assert.deepEqual(["pdf", "markdown", "text", ""].map(formatLabel), ["PDF", "Markdown", "Text", ""], "a format alone");
  const pdf = { format: "pdf", name: "main.pdf", hash: "p1", ext: ".pdf", at: "path" };
  assert.equal(pageFormat({ _doc: pdf, _fileName: "main.pdf" }), pdf, "its document's");
  assert.deepEqual(pageFormat({ _doc: null, _fileName: "refs.bib" }), { format: "text", language: "bibtex" }, "its file name's");
  assert.deepEqual(pageFormat({ _doc: null, _fileName: "" }), { format: "markdown", language: null }, "a note made in Gamma");
  assert.equal(formatLabel(pageFormat({ _doc: null, _fileName: "notes.md" })), "Markdown");
});

test("defaultPageTitle: the document's name, else its address's tail, else Untitled", () => {
  assert.equal(defaultPageTitle(null), "Untitled");
  assert.equal(defaultPageTitle({ name: "paper.pdf" }), "paper.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/a%20b.pdf" }), "a b.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/" }), "Untitled");
  assert.equal(defaultPageTitle({ name: "", url: "/api/uploads/d1.pdf" }), "d1.pdf", "a stored file without a name");
});

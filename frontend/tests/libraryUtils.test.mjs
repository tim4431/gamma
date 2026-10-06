// Pure library rules: the folder and label trees of the listing, then the
// dates, attachments and links.
import assert from "node:assert/strict";
import { test } from "node:test";
import { generateKeyBetween } from "fractional-indexing";
import {
  NO_LABEL, NO_LABEL_TITLE, addToFolder, childFolders, defaultPageTitle, filedIn, findNamed, findPageForUrl,
  firstFolderPath, folderChain, folderEntries, folderNeighbour, folderPath, folderPosition, folderPositions, folderSubtree, folderTargets,
  formatFullDate, formatRelativeTime, formatShortDate,
  inFolder, isFreshLibrary, labelName, libraryTree, normalizeLinkInput, pageAttachment,
  siblingFolders,
} from "../src/library/libraryUtils.js";

// The listing's trees: physics (with a subfolder whose name holds the path
// separator and a comma), then biology; two labels.
const [k1, k2] = [generateKeyBetween(null, null), generateKeyBetween(generateKeyBetween(null, null), null)];
const listing = {
  folders: { seq: 4, children: [
    { id: "phys", content: "Physics", position: k1, properties: { pinned: "2026-09-01T00:00:00Z" }, children: [
      { id: "qec", content: "QEC / codes, 2026", position: k1, properties: {}, children: [] },
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
    pinned: "", path: ["Physics", "QEC / codes, 2026"], children: [] });
  assert.equal(tree.folders.get("phys").pinned, "2026-09-01T00:00:00Z");
  assert.deepEqual([...tree.labels.values()].map((l) => l.name), ["llm", "Review"]);
  assert.equal(libraryTree().folders.size, 0, "no listing yet: empty trees");
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
  const pages = [
    { id: "p1", properties: { meta: { doi: "10.1000/ABC.1" } } },
    { id: "p2", properties: { source_url: "https://arxiv.org/pdf/2101.00001v2.pdf" } },
    { id: "p3", properties: { meta: { arxiv_id: "2202.02222" } } },
  ];
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1", pages), "p1");
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1.", pages), "p1", "trailing punctuation dropped");
  assert.equal(findPageForUrl("https://arxiv.org/abs/2101.00001", pages), "p2");
  assert.equal(findPageForUrl("arXiv:2202.02222", pages), "p3");
  assert.equal(findPageForUrl("https://example.com/paper.pdf", pages), null);
  assert.equal(findPageForUrl("", pages), null);
});

test("isFreshLibrary: no pages, or only seeded ones", () => {
  assert.equal(isFreshLibrary([]), true);
  assert.equal(isFreshLibrary([{ id: "w", properties: { seeded: "welcome" } }]), true, "the Welcome page alone");
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

test("pageAttachment and defaultPageTitle", () => {
  assert.equal(pageAttachment({ properties: {} }), null);
  // the stored copy's URL is derived from doc_id; a source_url of its own wins
  assert.deepEqual(pageAttachment({ properties: { doc_id: "d1", original_filename: "paper.pdf" } }),
    { kind: "pdf", id: "d1", url: "/api/uploads/d1.pdf", name: "paper.pdf" });
  assert.deepEqual(pageAttachment({ properties: { doc_id: "d1", source_url: "https://x.test/p.pdf" } }),
    { kind: "pdf", id: "d1", url: "https://x.test/p.pdf", name: "" });
  assert.deepEqual(pageAttachment({ properties: { source_url: "/api/uploads/d1.pdf" } }),
    { kind: "pdf", id: "", url: "/api/uploads/d1.pdf", name: "" });
  assert.equal(defaultPageTitle(null), "Untitled");
  assert.equal(defaultPageTitle({ name: "paper.pdf" }), "paper.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/a%20b.pdf" }), "a b.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/" }), "Untitled");
});

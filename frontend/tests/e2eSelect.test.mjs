// The browser suite's change-based selection (tests/e2e/select.mjs): the
// groups cover every scenario file once, the rules name real groups, and
// every source file is placed by a rule of its own, never by the final
// catch-all that selects nothing; and the shards CI splits the suite into.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { GROUPS, RULES, ruleFor, selectGroups, shardOf } from "./e2e/select.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const ids = GROUPS.map((g) => g.id);

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...walk(rel));
    else out.push(rel);
  }
  return out;
}

test("every scenario file is in exactly one group, and every group's files exist", () => {
  const scenarios = fs.readdirSync(path.join(ROOT, "frontend/tests/e2e/scenarios")).filter((f) => f.endsWith(".mjs"));
  const listed = GROUPS.flatMap((g) => g.files);
  assert.deepEqual([...listed].sort(), [...scenarios].sort());
  assert.equal(new Set(ids).size, ids.length, "group ids are unique");
});

test("rules name only known groups", () => {
  for (const [glob, groups] of RULES) {
    if (groups === "all") continue;
    for (const id of groups) assert.ok(ids.includes(id), `${glob} names unknown group ${id}`);
  }
});

test("every app source file is placed by a rule, not the catch-all", () => {
  const files = [...walk("frontend/src"), ...walk("frontend/public"), ...walk("backend/gamma")];
  const unplaced = files.filter((f) => ruleFor(f).glob === "**");
  assert.deepEqual(unplaced, [], "add a rule in tests/e2e/select.mjs for these");
});

test("a change selects the groups that exercise it", () => {
  const pick = (files, lines) => selectGroups(files, lines).groups;
  assert.deepEqual(pick(["frontend/src/guide/useGuide.js"]).sort(), ["contextual-guide", "guide", "triggered-guide"]);
  assert.deepEqual(pick(["docs/dev/debugging.md", "desktop/main.js", "backend/tests/test_ink.py"]), []);
  assert.equal(pick(["frontend/src/app/App.jsx"]).length, GROUPS.length);
  // the pens' "Use this browser's pens instead?" (prefs.js useProfileSync, App's onReplaced) is ink.mjs's
  for (const file of ["frontend/src/app/prefs.js", "frontend/src/app/prefDefs.js"]) assert.ok(pick([file]).includes("ink"), file);
  assert.deepEqual(pick(["backend/gamma/ink.py"]).sort(), ["ink", "ink-editing", "ink-shapes", "notebooks", "transfers", "triggered-guide"]);
  // the Notability import is an export's groups; its fixture writer is the transfers scenario's
  assert.deepEqual(pick(["backend/gamma/notability_import.py"]).sort(), ["diagrams", "files", "ink", "transfers"]);
  assert.deepEqual(pick(["backend/tests/notability_fixtures.py"]), ["transfers"]);
  assert.deepEqual(pick(["backend/gamma/goodnotes_import.py"]).sort(), ["diagrams", "files", "ink", "transfers"]);
  assert.deepEqual(pick(["backend/tests/goodnotes_fixtures.py"]).sort(), ["ipad", "transfers"]);
  // and the note that scenario imports goes through ink.py (its strokes: encode_points, the
  // stroke file's checks, group_files) and notebook.py (its paper page: normalize_paper, paper_ops)
  for (const file of ["backend/gamma/ink.py", "backend/gamma/notebook.py"]) assert.ok(pick([file]).includes("transfers"), file);
  // a native app's opened files are handed over by the ipad and android groups' stand-in shells alone,
  // and the images among them are placed by usePictures (placeInView), which keeps src/markup/'s groups
  assert.deepEqual(pick(["frontend/src/transfers/openedFiles.js"]).sort(), ["android", "ipad"]);
  // the Android app: its page-side scripts and the chrome pages are the android group's; what its host
  // changes in the web (the pen's claim and gestures, the Stylus settings, no folder picker, the saves)
  // selects it beside the groups those files always had
  for (const file of ["android/app/src/main/assets/shell/host.js", "android/app/src/main/assets/shell/chrome.js", "desktop/ui/bar.html",
    "desktop/ui/launcher.html", "desktop/ui/theme.css"]) assert.deepEqual(pick([file]), ["android"], file);
  for (const file of ["frontend/src/ink/usePencil.js", "frontend/src/settings/SettingsDialog.jsx", "frontend/src/transfers/folderZip.js",
    "frontend/src/transfers/FileChip.jsx", "frontend/src/tasks/tasksApi.js"]) assert.ok(pick([file]).includes("android"), file);
  for (const file of ["frontend/src/transfers/folderZip.js", "frontend/src/transfers/FileChip.jsx"]) {
    assert.deepEqual(pick([file]).filter((id) => id !== "android").sort(), pick(["frontend/src/transfers/exportApi.js"]).sort(), file);
  }
  assert.deepEqual(pick(["android/app/src/main/kotlin/com/gammapdf/gamma/MainActivity.kt"]), [], "the app's Kotlin has its own tests");
  // the shells' stand-ins are the ipad and android groups', and the pen's, which runs on both
  assert.deepEqual(pick(["frontend/tests/e2e/shells.mjs"]).sort(), ["android", "ipad", "pen"]);
  const markupGroups = pick(["frontend/src/markup/picture.js"]);
  assert.ok(!markupGroups.includes("ipad"), "the rest of src/markup/ selects no ipad");
  assert.deepEqual(pick(["frontend/src/markup/usePictures.js"]).sort(), [...markupGroups, "ipad"].sort());
  // a comment card stands in the page's box, where the ink layer and the text boxes leave its presses alone
  for (const file of ["frontend/src/ink/InkLayer.jsx", "frontend/src/markup/TextBoxLayer.jsx"]) assert.ok(pick([file]).includes("comments"), file);
  // the handwriting's owners in src/ink/: the ink-live socket, the build fence and the save are
  // collab's too; what Play disarms, clears, stops or lists is the recording's; the Pencil and the
  // strip keys are the iPad's; and each keeps every group the rest of src/ink/ selects
  const ink = (name) => `frontend/src/ink/${name}`;
  const inkGroups = pick([ink("ink.js")]);
  assert.ok(!inkGroups.includes("collab") && !inkGroups.includes("recording"), "the rest of src/ink/ selects neither");
  const owners = {
    collab: ["inkLive.js", "useInkLive.js", "buildFence.js", "inkSaver.js", "useInkSave.js", "useInkDocument.js", "inkHistory.js", "inkEdits.js"],
    recording: ["useInkStrip.js", "stripState.js", "useInkSelection.js", "selectionEdits.js", "useInkSurfaces.js", "inkSurfaces.js",
      "useInkDocument.js", "inkHistory.js", "inkEdits.js"],
    ipad: ["usePencil.js", "useStripKeys.js", "pencilActions.js", "stripKeys.js", "inkCommands.js"],
  };
  for (const [id, names] of Object.entries(owners)) {
    for (const name of names) {
      const groups = pick([ink(name)]);
      assert.ok(groups.includes(id), `${name} selects ${id}`);
      for (const g of inkGroups) assert.ok(groups.includes(g), `${name} keeps ${g}`);
    }
  }
  // an owner's pure parts select its groups; the commands' context is ink-editing's and the iPad's
  // (Ctrl+C / X / V / D / A), not every group as App's does
  const parts = { "stripState.js": ["useInkStrip.js"], "inkHistory.js": ["useInkDocument.js"], "inkEdits.js": ["useInkDocument.js", "useInkStrokes.js"],
    "selectionEdits.js": ["useInkSelection.js"], "inkSurfaces.js": ["useInkSurfaces.js"] };
  for (const [part, from] of Object.entries(parts)) assert.deepEqual(pick([ink(part)]), pick(from.map(ink)), `${part} selects ${from.join(" and ")}'s groups`);
  assert.deepEqual(pick([ink("inkCommands.js")]), inkGroups);
  assert.ok(inkGroups.includes("ink-editing") && inkGroups.includes("ipad"));
  // the next sheet's rule moved into notebook.js: a notebook change still selects notebooks
  assert.ok(pick(["frontend/src/notebook/notebook.js"]).includes("notebooks"));
  // the chat's pictures of handwriting are the AI modules' groups
  assert.deepEqual(pick(["backend/gamma/ink_view.py"]).sort(), pick(["backend/gamma/ai_context.py"]).sort());
  // Redraw as shapes and Convert to text: their server is the ink-ai scenario's alone (its routes stubbed
  // there, every other scenario never calling them); the client's, in src/ink/, is every ink group's with it
  for (const file of ["backend/gamma/ink_ai.py", "backend/gamma/routers/ink_ai.py"]) assert.deepEqual(pick([file]), ["ink-ai"], file);
  for (const file of ["frontend/src/ink/inkAi.js", "frontend/src/ink/inkAiClient.js", "frontend/src/ink/inkSvg.js", "frontend/src/ink/useInkSelection.js"]) {
    assert.ok(pick([file]).includes("ink-ai"), file);
  }
  // the Zotero link: its engine, its Zotero client, its page surfaces and the fake Zotero with the
  // library it serves are the zotero-link scenario's alone; the surfaces it shares with the clones,
  // the account row Settings draws and the import's note rules the engine reads select it too
  for (const file of ["backend/gamma/zotero_sync_items.py", "backend/gamma/zotero_schema.json", "backend/gamma/zotero_stream.py",
    "backend/tests/zotero_fake.py", "backend/tests/zotero_fixtures.py", "frontend/src/collaboration/ZoteroPage.jsx",
    "frontend/src/settings/ZoteroLinkDialog.jsx"]) assert.deepEqual(pick([file]), ["zotero-link"], file);
  for (const file of ["frontend/src/collaboration/MirrorPopover.jsx", "frontend/src/collaboration/MergeResolver.jsx",
    "frontend/src/settings/SettingsZotero.jsx", "frontend/src/transfers/zoteroApi.js", "backend/gamma/sync_clock.py",
    "backend/gamma/routers/zotero_sync.py", "backend/gamma/zotero_notes.py"]) assert.ok(pick([file]).includes("zotero-link"), file);
  assert.ok(pick(["backend/gamma/zotero_sync.py"]).includes("transfers"), "an import asks whether a link keeps its workspace");
  assert.ok(!pick(["backend/gamma/zotero_export.py"]).includes("zotero-link"), "the export is no part of the link");
  // text in search and the chat: the index, the one text reader, the path pair, the line link's
  // readers, the Files group and the code view's mark select the group that drives them all
  for (const file of ["backend/gamma/search/indexer.py", "backend/gamma/documents/text.py", "backend/gamma/routers/search.py",
    "backend/gamma/work_folders/paths.py", "backend/gamma/mcp_links.py", "backend/gamma/ai_tools.py", "frontend/src/search/SearchPanel.jsx",
    "frontend/src/documents/viewers/textMark.js", "frontend/src/documents/viewers/TextViewer.jsx"]) assert.ok(pick([file]).includes("text-search"), file);
  assert.ok(pick(["backend/gamma/work_folders/paths.py"]).includes("feedback"), "the feedback entries name their files by it");
  // anchors in text: the ladder, the shared write, the code view's marks and popup, the margin, the entries and the vault's round
  for (const file of ["backend/gamma/documents/text_anchors.py", "backend/gamma/highlight_relocate.py", "backend/gamma/documents/text.py",
    "backend/gamma/feedback/entries.py", "backend/gamma/vault/sync.py", "frontend/src/documents/viewers/textAnchors.js",
    "frontend/src/documents/viewers/TextTip.jsx", "frontend/src/comments/marginSlot.jsx", "frontend/src/chat/ChatDock.jsx"]) assert.ok(pick([file]).includes("text-anchors"), file);
  assert.ok(pick(["backend/gamma/highlight_relocate.py"]).includes("notes-pdf-share"), "the PDF's run is the same write");
  // the explorer: the Files window, quick open's files, the crumbs, links by path and the reads behind them
  for (const file of ["frontend/src/explorer/FileTree.jsx", "frontend/src/explorer/useFileSearch.js", "frontend/src/explorer/workPaths.js",
    "frontend/src/library/QuickOpen.jsx", "frontend/src/editor/PageCrumbs.jsx", "frontend/src/editor/PathLink.jsx",
    "backend/gamma/work_folders/file_search.py", "backend/gamma/work_folders/paths.py", "backend/gamma/routers/folders.py",
    "backend/gamma/vault/links.py"]) assert.ok(pick([file]).includes("explorer"), file);
  assert.deepEqual(pick(["backend/tests/seed_work_folder.py"]), ["explorer"], "the seed is the explorer group's alone");
  assert.ok(pick(["frontend/src/editor/PathLink.jsx"]).includes("notes-pdf-share"), "a note's relative link is drawn there");
  // a scenario helper selects the scenarios importing it
  const notes = pick(["frontend/tests/e2e/scenarios/notes.mjs"]);
  for (const id of ["notes-pdf-share", "collab", "files", "diagrams"]) assert.ok(notes.includes(id), id);
  // a guide anchor on a changed line brings the tours in, whatever the file
  const anchored = pick(["frontend/src/sharing/SharePopover.jsx"], () => ['+  <button data-guide="share.copy">']);
  for (const id of ["guide", "contextual-guide", "triggered-guide", "publish"]) assert.ok(anchored.includes(id), id);
  assert.ok(!pick(["frontend/src/sharing/SharePopover.jsx"], () => ["+  <button>"]).includes("guide"));
});

test("the shards split a selection whole, in shares of about equal time", () => {
  const time = Object.fromEntries(GROUPS.map((g) => [g.id, g.s]));
  assert.ok(ids.every((id) => time[id] > 0), "every group has its time");
  for (const n of [1, 2, 3]) {
    const shards = Array.from({ length: n }, (_, k) => shardOf(ids, k + 1, n));
    assert.deepEqual(shards.flat().sort(), [...ids].sort(), `${n} shards: every group once`);
    const loads = shards.map((s) => s.reduce((sum, id) => sum + time[id], 0));
    assert.ok(Math.max(...loads) - Math.min(...loads) <= Math.max(...Object.values(time)), `${n} shards: ${loads}`);
  }
  // a shard keeps the selection's order
  const picked = ["guide", "ink", "trash", "settings"];
  const mine = shardOf(picked, 1, 2);
  assert.deepEqual(mine, picked.filter((id) => mine.includes(id)));
});

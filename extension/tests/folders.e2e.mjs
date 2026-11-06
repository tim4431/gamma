// Run with node extension/tests/folders.e2e.mjs (Playwright full Chromium).
// The folder and label fields of the popup and the options page against
// GET /api/library/folders' shape ({folders: [{id, path}], labels: [{id,
// name}]}) and what a save sends POST /api/clip: a picked or typed folder's
// id as `folder`, a typed new folder as `folder_path`, label names; the
// default folder is remembered by id per server, a path stored by an older
// version is sent once and replaced, and a long menu stays inside the popup.
// The tree: what it opens, its keys, and a folder named in place, made through
// POST /api/pages/folders/ops, which a save waits for and which, refused, no
// paper is filed around.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import headed from "../../tools/headed.cjs";

const folders = [
  { id: "f-reading", path: ["Reading", "2026"] },
  { id: "f-read", path: ["Reading"] },
  { id: "f-papers", path: ["Papers"] },
];
// More labels than the room under the labels field holds.
const labels = ["physics", "optics", "atoms", "cavity", "spin", "noise", "clocks", "ions", "qubits"]
  .map((name, i) => ({ id: `l-${i}`, name }));
const clips = [];
const treeOps = [];       // the inserts POST /api/pages/folders/ops was sent
let treeOpsWait = null;   // holds them back, while a promise
let refuseTreeOps = false;
let listAnswer = null;    // stands in for the list: an answer, or "fail"
let clipped;
const http = createServer(async (req, res) => {
  if (!req.url.startsWith("/api/")) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end('<html><head><title>Test paper</title><meta name="citation_doi" content="10.1234/testing"></head><body>Paper</body></html>');
    return;
  }
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/session") res.end('{"user":"alice"}');
  else if (req.url === "/api/library/folders") {
    if (listAnswer === "fail") { res.writeHead(500); res.end('{"detail":"no"}'); }
    else res.end(JSON.stringify(listAnswer || { folders, labels }));
  } else if (req.url === "/api/pages/folders/ops") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const { ops } = JSON.parse(body);
    treeOps.push(...ops);
    await treeOpsWait;
    if (refuseTreeOps) { res.writeHead(403); res.end('{"detail":"read-only here"}'); return; }
    for (const { id, parent, content } of ops) {
      const above = folders.find((f) => f.id === parent);
      if (!folders.some((f) => f.id === id)) folders.unshift({ id, path: [...(above ? above.path : []), content] });
    }
    res.end(JSON.stringify({ seq: 1, at: "", ops }));
  } else if (req.url === "/api/clip") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const clip = JSON.parse(body);
    // The server makes a folder named by path (one typed in the popup).
    if (clip.folder_path) folders.unshift({ id: `f-${folders.length}`, path: clip.folder_path.split("/") });
    clips.push(clip);
    res.end(JSON.stringify({ block_id: "p1", doc_id: "", title: "Test paper", existed: false, open_url: "/?block=p1", folders: [], labels: [] }));
    clipped?.();
  } else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
});
await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${http.address().port}`;

const profile = await mkdtemp(path.join(tmpdir(), "gamma-folders-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: !headed,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  // A default folder as an older version stored it: a path.
  await worker.evaluate((server) => chrome.storage.sync.set({ server, folder: "Reading/2026" }), origin);
  const sync = () => worker.evaluate(() => chrome.storage.sync.get(["defaultFolders", "folder"]));
  const id = new URL(worker.url()).host;
  const errors = [];
  // The folder field's chip ("" for none: the library root).
  const folderChip = (page) => page.evaluate(() => document.querySelector("#folder-box .categoryTagText")?.textContent || "");
  const menuTexts = (page, menu) => page.locator(`${menu} .ctxMenuText`).allTextContents();
  const row = (page, text) => page.locator("#folder-menu .ctxMenuItem", { hasText: text });
  // Until the stored default folder is `id`: storage answers in a promise,
  // which waitForFunction would take for a yes.
  async function defaultBecomes(id) {
    for (let i = 0; i < 200 && (await sync()).defaultFolders?.[origin] !== id; i++) await new Promise((resolve) => setTimeout(resolve, 25));
    assert.equal((await sync()).defaultFolders?.[origin], id);
  }

  // Options: the stored path shows as its folder; the tree shows it open down
  // to that folder, checked; a pick stores the id.
  const options = await context.newPage();
  options.on("pageerror", (error) => errors.push(error.message));
  await options.goto(`chrome-extension://${id}/options.html`);
  await options.waitForFunction(() => !document.getElementById("folder").disabled);
  assert.equal(await folderChip(options), "Reading / 2026");
  await options.locator("#folder").click();
  assert.deepEqual(await menuTexts(options, "#folder-menu"), ["Reading", "2026", "Papers", "New folder…"]);
  assert.equal(await row(options, "2026").locator(".ctxMenuCheck").count(), 1);
  await row(options, "Papers").click();
  await options.waitForFunction(() => document.getElementById("save-status").textContent === "Saved.");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-papers" }, folder: "" });

  const paper = await context.newPage();
  await paper.goto(`${origin}/doi/10.1234/testing`);
  const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, paper.url());
  // Opens the popup on the paper with the form showing, once the folder
  // field shows `shown` (the default folder).
  async function popupForm(shown) {
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.waitForFunction((shown) => document.querySelector("#folder-box .categoryTagText")?.textContent === shown, shown);
    // Saved once already: "Add to another folder…" brings the form back.
    if (await popup.locator("#existing").isVisible()) await popup.locator("#refile").click();
    return popup;
  }
  // Presses Save and waits for the clip it sends.
  async function pressSave(popup) {
    const done = new Promise((resolve) => { clipped = resolve; });
    await popup.locator("#save").click();
    await done;
    await popup.locator("#existing-msg.ok").waitFor();
    await popup.close();
    return clips.at(-1);
  }
  // A folder chosen by `pickFolder` in the popup, saved; the clip it sends.
  async function popupSave(shown, pickFolder) {
    const popup = await popupForm(shown);
    await pickFolder(popup);
    return pressSave(popup);
  }

  // The popup starts at the default. With nothing typed the folder field shows
  // the tree, open only above the chosen folder; the field, like the labels,
  // suggests as you type: every other folder by path, matched name by name
  // ("rea/20" finds "Reading / 2026"), and what no folder's path matches as
  // a new folder. A picked
  // folder goes by id, labels by name (suggested from the {id, name} labels).
  let clip = await popupSave("Papers", async (popup) => {
    await popup.locator("#folder").click();
    assert.deepEqual(await menuTexts(popup, "#folder-menu"), ["Reading", "Papers", "New folder…"], "the tree's top, nothing open");
    await popup.locator("#folder").fill("rea/20");
    assert.deepEqual(await menuTexts(popup, "#folder-menu"), ["Reading / 2026", "New folder “rea / 20”"]);
    await popup.locator("#folder-menu .ctxMenuItem", { hasText: "Reading / 2026" }).click();
    assert.equal(await folderChip(popup), "Reading / 2026", "a pick replaces the folder");
    assert.equal(await popup.locator("#folder").inputValue(), "");
    assert.equal(await popup.locator("#folder-menu").isVisible(), false, "and closes the menu: one folder");
    await popup.locator("#folder").click();
    assert.equal(await popup.locator("#folder-menu").isVisible(), true, "a click reopens it");
    await popup.locator("#folder").press("Escape");
    await popup.locator("#labels").fill("ph");
    await popup.locator("#label-menu .ctxMenuItem", { hasText: "physics" }).click();
    // The labels' menu stays open for the next one, until Escape.
    assert.equal(await popup.locator("#label-menu").isVisible(), true);
    await popup.locator("#labels").press("Escape");
  });
  assert.equal(clip.folder, "f-reading");
  assert.equal(clip.folder_path, "");
  assert.deepEqual(clip.labels, ["physics"]);
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "f-reading" });

  // A typed new folder, committed with Enter, goes as folder_path and is
  // remembered by its new id.
  clip = await popupSave("Reading / 2026", async (popup) => {
    await popup.locator("#folder").fill("Inbox/new");
    await popup.locator("#folder").press("Enter");
    assert.equal(await folderChip(popup), "Inbox / new");
    assert.match(await popup.locator("#folder-box .categoryTag").getAttribute("title"), /new folder/);
  });
  assert.equal(clip.folder, "");
  assert.equal(clip.folder_path, "Inbox/new");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "f-3" });

  // The shortcut and context menu save into the default by id.
  const save = () => options.evaluate(() => chrome.runtime.sendMessage({ type: "save", candidate: {
    kind: "doi", doi: "10.1234/testing", source_url: location.href,
  } }));
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "f-3");

  // Typed but not committed still counts, as a label does: an existing
  // folder's path (any case) is that folder.
  clip = await popupSave("Inbox / new", async (popup) => {
    await popup.locator("#folder").fill("papers");
  });
  assert.deepEqual({ folder: clip.folder, folder_path: clip.folder_path }, { folder: "f-papers", folder_path: "" });

  // Backspace in an empty field removes the folder: the library root.
  clip = await popupSave("Papers", async (popup) => {
    await popup.locator("#folder").focus();
    await popup.locator("#folder").press("Backspace");
    assert.equal(await folderChip(popup), "");
    assert.deepEqual(await popup.locator("#folder-menu .ctxMenuCheck").count(), 0, "nothing checked: the root");
    assert.equal(await popup.locator("#folder").getAttribute("placeholder"), "Library root");
    await popup.locator("#folder").press("Escape");
  });
  assert.deepEqual({ folder: clip.folder, folder_path: clip.folder_path }, { folder: "", folder_path: "" });
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "" });

  // In a window as tall as the popup, as Chrome sizes it, a menu longer
  // than the room below its field opens on the side with more room, cut to
  // fit and scrolling, and nothing hangs past the popup's bottom.
  {
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.locator("#existing").waitFor();
    await popup.locator("#refile").click();
    await popup.setViewportSize({ width: 360, height: await popup.evaluate(() => document.body.offsetHeight) });
    await popup.locator("#labels").click();
    const box = await popup.evaluate(() => {
      const menu = document.getElementById("label-menu");
      const r = menu.getBoundingClientRect();
      const field = document.getElementById("labels-box").getBoundingClientRect();
      return { up: r.bottom <= field.top, top: r.top, bottom: r.bottom, height: innerHeight,
               below: innerHeight - field.bottom, above: field.top, scrolls: menu.scrollHeight > menu.clientHeight,
               page: document.documentElement.scrollHeight };
    });
    const what = JSON.stringify(box);
    assert.ok(box.below < 200, `the field leaves too little room below for the whole menu: ${what}`);
    assert.equal(box.up, box.above > box.below, `the side with more room: ${what}`);
    assert.ok(box.top >= 0 && box.bottom <= box.height, `the menu is inside the popup: ${what}`);
    assert.ok(box.scrolls, `cut to fit, it scrolls: ${what}`);
    assert.ok(box.page <= box.height, `nothing hangs past the popup's bottom: ${what}`);
    assert.equal(await popup.locator("#label-menu .ctxMenuItem").count(), 9, "eight labels and New label…");
    await popup.locator("#label-menu .menuLast").scrollIntoViewIfNeeded();
    assert.ok(await popup.evaluate(() => {
      const last = document.querySelector("#label-menu .menuLast").getBoundingClientRect();
      const menu = document.getElementById("label-menu").getBoundingClientRect();
      return last.top >= menu.top && last.bottom <= menu.bottom;
    }), "New label… stays in sight over the list that scrolls");
    await popup.close();
  }

  // A path an older version stored is sent once, then replaced by its id.
  await worker.evaluate(() => chrome.storage.sync.set({ defaultFolders: {}, folder: "Old/path" }));
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "");
  assert.equal(clips.at(-1).folder_path, "Old/path");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-4" }, folder: "" });
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "f-4");

  // The popup shows a stored path no folder has as a new folder.
  await worker.evaluate(() => chrome.storage.sync.set({ defaultFolders: {}, folder: "Fresh" }));
  clip = await popupSave("Fresh", async (popup) => {
    assert.match(await popup.locator("#folder-box .categoryTag").getAttribute("title"), /new folder/);
  });
  assert.equal(clip.folder_path, "Fresh");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-5" }, folder: "" });

  // ---------- the tree ----------

  folders.splice(0, folders.length,
    { id: "t-lab", path: ["Lab"] },
    { id: "t-clocks", path: ["Lab", "Clocks"] },
    { id: "t-lattice", path: ["Lab", "Clocks", "Lattice"] },
    { id: "t-ions", path: ["Lab", "Ions"] },
    { id: "t-slash", path: ["a/b"] },
    { id: "t-twin-1", path: ["Twin"] },
    { id: "t-twin-2", path: ["Twin"] },
    { id: "t-papers", path: ["Papers"] });
  const useDefault = (folder) => worker.evaluate(([folder, origin]) => chrome.storage.sync.set({ defaultFolders: { [origin]: folder } }), [folder, origin]);
  const nameInput = (page) => page.locator("#folder-menu .ctxMenuInput");
  const focused = (page) => page.evaluate(() => document.activeElement?.id || document.activeElement?.className);
  const newChip = (page) => page.locator("#folder-box .categoryTag").getAttribute("title");

  // Open above the chosen folder and no further; a chevron folds and picks nothing.
  await useDefault("t-lattice");
  let popup = await popupForm("Lab / Clocks / Lattice");
  await popup.locator("#folder").click();
  assert.deepEqual(await menuTexts(popup, "#folder-menu"), ["Lab", "Clocks", "Lattice", "Ions", "a/b", "Twin", "Twin", "Papers", "New folder…"]);
  assert.equal(await row(popup, "Lattice").locator(".ctxMenuCheck").count(), 1);
  assert.equal(await row(popup, "Lattice").locator(".treeAdd").isVisible(), false, "the \"+\" shows with the highlight");
  await row(popup, "Clocks").locator(".treeTwist").click();
  assert.deepEqual((await menuTexts(popup, "#folder-menu")).slice(0, 3), ["Lab", "Clocks", "Ions"]);
  assert.equal(await folderChip(popup), "Lab / Clocks / Lattice", "folding picks nothing");
  assert.equal(await popup.locator("#folder-menu").isVisible(), true);
  await row(popup, "Lab").locator(".treeTwist").click();
  assert.deepEqual((await menuTexts(popup, "#folder-menu")).slice(0, 2), ["Lab", "a/b"]);
  await popup.close();

  // The keys, the focus staying in the field: Right opens, then goes in; Left
  // closes, then goes up; "+" names a folder there. Its op names the parent by
  // id, and a save files by the new id, never by a path.
  await useDefault("t-papers");
  clip = await popupSave("Papers", async (popup) => {
    const highlighted = () => popup.locator("#folder-menu .selected .ctxMenuText").textContent();
    await popup.locator("#folder").click();
    await popup.locator("#folder").press("ArrowDown");
    assert.equal(await highlighted(), "Lab");
    await popup.locator("#folder").press("ArrowRight");
    assert.deepEqual((await menuTexts(popup, "#folder-menu")).slice(0, 3), ["Lab", "Clocks", "Ions"]);
    assert.equal(await highlighted(), "Lab", "Right opened it");
    await popup.locator("#folder").press("ArrowRight");
    assert.equal(await highlighted(), "Clocks", "Right went in");
    await popup.locator("#folder").press("ArrowLeft");
    assert.equal(await highlighted(), "Lab", "Left went up");
    await popup.locator("#folder").press("ArrowLeft");
    assert.deepEqual((await menuTexts(popup, "#folder-menu")).slice(0, 2), ["Lab", "a/b"]);
    assert.equal(await highlighted(), "Lab", "Left closed it");
    await popup.locator("#folder").press("ArrowRight");
    await popup.locator("#folder").press("ArrowRight");
    await popup.locator("#folder").press("ArrowDown");
    assert.equal(await highlighted(), "Ions");
    await popup.locator("#folder").press("+");
    assert.equal(await nameInput(popup).inputValue(), "", "the key is no text");
    await nameInput(popup).fill("Traps");
    await nameInput(popup).press("Enter");
    assert.equal(await folderChip(popup), "Lab / Ions / Traps");
    assert.equal(await focused(popup), "folder", "the focus is back in the field");
    assert.equal(await popup.locator("#folder-menu").isVisible(), false);
    assert.match(await newChip(popup), /a new folder/);
  });
  assert.deepEqual(treeOps.at(-1), { op: "insert", id: clip.folder, parent: "t-ions", content: "Traps" });
  assert.equal(clip.folder_path, "");

  // "New folder…" names one at the top, where the row was; a mouse "+" under a
  // parent whose name holds a slash, or which shares its name, goes by its id.
  clip = await popupSave("Lab / Ions / Traps", async (popup) => {
    await popup.locator("#folder").click();
    await row(popup, "New folder…").click();
    assert.equal(await nameInput(popup).evaluate((el) => el === document.activeElement), true);
    await nameInput(popup).fill("Top");
    await nameInput(popup).press("Enter");
    assert.equal(await folderChip(popup), "Top");
  });
  assert.deepEqual(treeOps.at(-1), { op: "insert", id: clip.folder, parent: "folders", content: "Top" });
  for (const [parent, nth, id] of [["a/b", 0, "t-slash"], ["Twin", 1, "t-twin-2"]]) {
    await useDefault("t-papers");
    const popup = await popupForm("Papers");
    await popup.locator("#folder").click();
    await row(popup, parent).nth(nth).hover();
    await row(popup, parent).nth(nth).locator(".treeAdd").click();
    await nameInput(popup).fill("c/d");  // one name, slash and all
    await nameInput(popup).press("Enter");
    assert.equal(await folderChip(popup), `${parent} / c/d`);
    clip = await pressSave(popup);
    assert.deepEqual(treeOps.at(-1), { op: "insert", id: clip.folder, parent: id, content: "c/d" });
  }

  // A name a sibling has, in any case, is that sibling: nothing is made. A
  // name input left empty, or left by Escape, makes nothing either.
  let sent = treeOps.length;
  await useDefault("t-papers");
  clip = await popupSave("Papers", async (popup) => {
    await popup.locator("#folder").click();
    await row(popup, "Lab").locator(".treeTwist").click();
    await row(popup, "Lab").hover();
    await row(popup, "Lab").locator(".treeAdd").click();
    await nameInput(popup).fill("IONS");
    await nameInput(popup).press("Enter");
    assert.equal(await folderChip(popup), "Lab / Ions");
    assert.equal(await popup.locator("#folder-box .categoryTag").getAttribute("title"), "Lab / Ions");
  });
  assert.equal(clip.folder, "t-ions");
  assert.equal(treeOps.length, sent);

  popup = await popupForm("Lab / Ions");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).press("Enter");
  assert.equal(await nameInput(popup).count(), 0, "empty, it is cancelled");
  assert.equal(await focused(popup), "folder");
  assert.equal(treeOps.length, sent);
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Dropped");
  const prevented = await nameInput(popup).evaluate((el) => {
    const key = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    el.dispatchEvent(key);
    return key.defaultPrevented;
  });
  assert.equal(prevented, true, "Chrome would close the popup");
  assert.equal(await nameInput(popup).count(), 0);
  assert.equal(await focused(popup), "folder");
  assert.equal(await popup.locator("#folder-menu").isVisible(), true, "Escape left the name, not the menu");
  assert.equal(await popup.locator("#folder").press("Escape").then(() => popup.locator("#folder-menu").isVisible()), false);

  // A composing Enter belongs to the input method, in either input.
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("中文");
  const composingEnter = (el) => el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true }));
  await nameInput(popup).evaluate(composingEnter);
  assert.equal(await nameInput(popup).inputValue(), "中文");
  assert.equal(treeOps.length, sent);
  // A blur the input keeps the focus through is the window's, which ends nothing.
  await nameInput(popup).evaluate((el) => el.dispatchEvent(new FocusEvent("blur")));
  assert.equal(await nameInput(popup).count(), 1);
  assert.equal(treeOps.length, sent);
  // Using another row drops the name: that row is the choice.
  await row(popup, "Papers").click();
  assert.equal(await folderChip(popup), "Papers");
  assert.equal(treeOps.length, sent);
  await popup.locator("#folder").fill("中");
  await popup.locator("#folder").evaluate(composingEnter);
  assert.equal(await popup.locator("#folder").inputValue(), "中", "the field's own Enter too");
  await popup.locator("#folder").fill("");

  // Tab goes on to the labels, through no row of the menu.
  await popup.locator("#folder").click();
  await popup.locator("#folder").press("Tab");
  assert.equal(await focused(popup), "labels");
  await popup.close();

  // A chip's × with a name input open cancels the name, as another row does:
  // nothing is made, and the menu and the field work on.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Typed");
  await popup.locator("#folder-box .uiClose").click();
  assert.equal(await folderChip(popup), "");
  assert.equal(await nameInput(popup).count(), 0);
  assert.equal(await focused(popup), "folder");
  await popup.locator("#folder").press("Escape");
  assert.equal(await popup.locator("#folder-menu").isVisible(), false, "the menu closes as ever");
  assert.equal(treeOps.length, sent);
  // A press on the chip itself opens the menu, focused or not, its × aside.
  await popup.locator("#folder").fill("Papers");
  await popup.locator("#folder").press("Enter");
  await popup.locator("#labels").click();
  await popup.locator("#labels").press("Escape");
  await popup.locator("#folder-box .categoryTagText").click();
  assert.equal(await popup.locator("#folder-menu").isVisible(), true);
  await popup.locator("#folder").press("Escape");
  await popup.locator("#folder-box .categoryTagText").click();
  assert.equal(await popup.locator("#folder-menu").isVisible(), true, "again with the focus already there");
  await popup.close();

  // Leaving the input commits the name, as the app's New folder does.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Left");
  await popup.locator("#view-main .head").click();
  await popup.waitForFunction(() => document.querySelector("#folder-box .categoryTagText")?.textContent === "Left");
  await popup.close();
  assert.equal(treeOps.at(-1).content, "Left");
  sent = treeOps.length;

  // Pressing Save and closing the popup while the folder is on its way loses
  // nothing: the worker makes it and saves.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Closed");
  await nameInput(popup).press("Enter");
  let holding;
  treeOpsWait = new Promise((resolve) => { holding = resolve; });
  let landed = new Promise((resolve) => { clipped = resolve; });
  await popup.locator("#save").click();
  await popup.close();
  holding();
  treeOpsWait = null;
  await landed;
  assert.equal(clips.at(-1).folder, treeOps.at(-1).id);
  assert.equal(treeOps.at(-1).content, "Closed");
  // The worker goes on to remember it, past the clip.
  await defaultBecomes(clips.at(-1).folder);

  // A save waits for the folder it names: held, nothing is clipped until it is
  // made. Save's own click ends the name input it was typed in.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Held");
  let release;
  treeOpsWait = new Promise((resolve) => { release = resolve; });
  let done = new Promise((resolve) => { clipped = resolve; });
  await popup.locator("#save").focus();
  assert.equal(await folderChip(popup), "Held", "leaving the input set the chip at once");
  await popup.locator("#save").press("Enter");
  let saved = clips.length;
  await popup.waitForFunction(() => !document.getElementById("progress").classList.contains("hidden"));
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.equal(clips.length, saved, "no clip before the folder is made");
  release();
  treeOpsWait = null;
  await done;
  assert.equal(clips.at(-1).folder, treeOps.at(-1).id);
  assert.equal(clips.at(-1).folder_path, "");
  await popup.close();

  // Refused while Save waits: no clip, the reason shown, the chip what was asked
  // for; Save again sends the same id, and the paper lands there.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Refused");
  refuseTreeOps = true;
  await popup.locator("#save").focus();
  await popup.locator("#save").press("Enter");
  await popup.locator("#result.msg.err", { hasText: "Couldn't make the folder “Refused”" }).waitFor();
  saved = clips.length;
  const refused = treeOps.at(-1).id;
  assert.equal(await folderChip(popup), "Refused");
  refuseTreeOps = false;
  done = new Promise((resolve) => { clipped = resolve; });
  await popup.locator("#save").click();
  await done;
  await popup.locator("#existing-msg.ok").waitFor();
  clip = clips.at(-1);
  assert.equal(clips.length, saved + 1);
  assert.equal(clip.folder, refused);
  assert.equal(treeOps.at(-1).id, refused, "the same id, so one folder");
  // Only the save made it, and the tree has it all the same: its name is taken.
  sent = treeOps.length;
  await popup.locator("#refile").click();
  await popup.locator("#folder").click();
  assert.ok((await menuTexts(popup, "#folder-menu")).includes("Refused"));
  await popup.close();
  assert.equal(treeOps.length, sent);

  // Refused before Save is pressed: the same, it does not read as chosen.
  await useDefault("t-papers");
  popup = await popupForm("Papers");
  await popup.locator("#folder").click();
  await row(popup, "New folder…").click();
  await nameInput(popup).fill("Early");
  refuseTreeOps = true;
  saved = clips.length;
  await nameInput(popup).press("Enter");
  await popup.locator("#result.msg.err", { hasText: "Couldn't make the folder “Early”" }).waitFor();
  await popup.locator("#save").click();
  await popup.waitForFunction(() => document.getElementById("progress").classList.contains("hidden") && !document.getElementById("save").disabled);
  assert.equal(clips.length, saved, "refused again, so still nothing is clipped");
  await popup.close();
  refuseTreeOps = false;

  // A list that can't be read says nothing of the default, which stays and is
  // saved into; with no tree to place it in, no folder can be named. So too
  // with the bare paths a server from before folder ids lists.
  for (const answer of ["fail", { folders: ["Quantum", "Quantum/Atoms"], labels: [] }]) {
    await useDefault("t-papers");
    listAnswer = answer;
    clip = await popupSave("Default folder", async (popup) => {
      await popup.locator("#folder").click();
      assert.equal(await popup.locator("#folder-menu").isVisible(), false);
    });
    listAnswer = null;
    assert.equal(clip.folder, "t-papers");
    assert.deepEqual((await sync()).defaultFolders, { [origin]: "t-papers" });
  }

  // Options has no Save: a new folder is stored once it exists; refused, the
  // field goes back to the stored default.
  await options.reload();
  await options.waitForFunction(() => document.querySelector("#folder-box .categoryTagText")?.textContent === "Papers");
  await options.locator("#folder").click();
  await row(options, "Lab").hover();
  await row(options, "Lab").locator(".treeAdd").click();
  await nameInput(options).fill("Stored");
  treeOpsWait = new Promise((resolve) => { release = resolve; });
  await nameInput(options).press("Enter");
  assert.equal(await folderChip(options), "Lab / Stored");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "t-papers" }, "not before it exists");
  release();
  treeOpsWait = null;
  await options.waitForFunction(() => document.getElementById("save-status").textContent === "Saved.");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: treeOps.at(-1).id });
  const stored = (await sync()).defaultFolders[origin];
  await options.locator("#folder").click();
  await row(options, "New folder…").click();
  await nameInput(options).fill("Nope");
  refuseTreeOps = true;
  await nameInput(options).press("Enter");
  await options.waitForFunction(() => document.querySelector("#folder-box .categoryTagText")?.textContent === "Lab / Stored");
  refuseTreeOps = false;
  assert.match(await options.locator("#save-status").textContent(), /Couldn't make the folder “Nope”/);
  assert.deepEqual((await sync()).defaultFolders, { [origin]: stored });
  // Typed text only filters here: what matches nothing stores nothing, and goes.
  await options.locator("#folder").fill("no such folder");
  await options.locator("#folder").press("Enter");
  await options.locator("#labels").click();
  assert.equal(await options.locator("#folder").inputValue(), "");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: stored });
  assert.equal(await folderChip(options), "Lab / Stored");
  // Nor does it hide a new folder from its own answer: an existing folder's
  // path typed while the write is out, the new folder is still what is stored.
  await options.locator("#folder").click();
  await row(options, "New folder…").click();
  await nameInput(options).fill("Under text");
  treeOpsWait = new Promise((resolve) => { release = resolve; });
  await nameInput(options).press("Enter");
  await options.locator("#folder").fill("Papers");
  release();
  treeOpsWait = null;
  await options.locator("#folder").press("Tab");
  await options.waitForFunction(() => document.getElementById("save-status").textContent === "Saved.");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: treeOps.at(-1).id });
  // A chip's × with a name input open, here too: nothing is made or stored but the root.
  const treeOpsSoFar = treeOps.length;
  await options.locator("#folder").click();
  await row(options, "New folder…").click();
  await nameInput(options).fill("Dropped");
  await options.locator("#folder-box .uiClose").click();
  await defaultBecomes("");
  assert.equal(treeOps.length, treeOpsSoFar);
  // A stored old path no folder has shows as what it is, and × clears it.
  await worker.evaluate(() => chrome.storage.sync.set({ defaultFolders: {}, folder: "Gone/Old" }));
  await options.reload();
  await options.waitForFunction(() => document.querySelector("#folder-box .categoryTagText")?.textContent === "Gone / Old");
  await options.locator("#folder-box .uiClose").click();
  await defaultBecomes("");
  assert.equal((await sync()).folder, "");
  await useDefault(stored);

  // A new label in place: a chip, the menu open for the next, made by the
  // save under its name, a comma and all. Typed, a name no label has is offered
  // beside what matches.
  sent = treeOps.length;
  clip = await popupSave("Lab / Stored", async (popup) => {
    await popup.locator("#labels").click();
    await popup.locator("#label-menu .ctxMenuItem", { hasText: "New label…" }).click();
    const label = popup.locator("#label-menu .ctxMenuInput");
    await label.fill("a, b");
    await label.press("Enter");
    assert.deepEqual(await popup.locator("#labels-box .categoryTagText").allTextContents(), ["a, b"]);
    assert.equal(await label.evaluate((el) => el === document.activeElement), true, "ready for the next");
    await label.press("Escape");
    assert.equal(await focused(popup), "labels");
    await popup.locator("#labels").fill("ion");
    assert.deepEqual(await menuTexts(popup, "#label-menu"), ["ions", "New label “ion”"]);
    await popup.locator("#label-menu .ctxMenuItem", { hasText: "New label “ion”" }).click();
    await popup.locator("#labels").press("Escape");
  });
  assert.deepEqual(clip.labels, ["a, b", "ion"]);
  assert.equal(treeOps.length, sent, "a label is the save's to make");

  // A name no label has opens nothing, so Save, right under the field, takes it.
  await useDefault(stored);
  popup = await popupForm("Lab / Stored");
  await popup.setViewportSize({ width: 360, height: await popup.evaluate(() => document.body.offsetHeight) });
  await popup.locator("#labels").fill("zzz");
  assert.equal(await popup.locator("#label-menu").isVisible(), false);
  clip = await pressSave(popup);
  assert.deepEqual(clip.labels, ["zzz"]);

  // In a window as tall as the popup, a name input stays inside it, in sight.
  popup = await popupForm("Lab / Stored");
  await popup.setViewportSize({ width: 360, height: await popup.evaluate(() => document.body.offsetHeight) });
  await popup.locator("#folder").click();
  await row(popup, "Lab").hover();
  await row(popup, "Lab").locator(".treeAdd").click();
  const inside = await popup.evaluate(() => {
    const menu = document.getElementById("folder-menu").getBoundingClientRect();
    const name = document.querySelector("#folder-menu .ctxMenuInput").getBoundingClientRect();
    return { name: name.top >= menu.top && name.bottom <= menu.bottom, menu: menu.top >= 0 && menu.bottom <= innerHeight,
             page: document.documentElement.scrollHeight <= innerHeight };
  });
  assert.deepEqual(inside, { name: true, menu: true, page: true });
  await popup.close();

  assert.deepEqual(errors, []);
  console.log("PASS: folder field by id and path, label names, new folders, the per-server default, the stored-path fallback, menus inside the popup, and the tree: its keys, folders made in place, a save waiting for one, a refusal filing nothing.");
} finally {
  await context?.close();
  http.close();
  await rm(profile, { recursive: true, force: true });
}

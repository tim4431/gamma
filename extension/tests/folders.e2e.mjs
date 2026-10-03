// Run with node extension/tests/folders.e2e.mjs (Playwright full Chromium).
// The folder and label pickers against GET /api/library/folders' shape
// ({folders: [{id, path}], labels: [{id, name}]}) and what a save sends
// POST /api/clip: a picked folder's id as `folder`, a typed new folder as
// `folder_path`, label names; the default folder is remembered by id per
// server, and a path stored by an older version is sent once and replaced.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const folders = [
  { id: "f-reading", path: ["Reading", "2026"] },
  { id: "f-papers", path: ["Papers"] },
];
const clips = [];
let clipped;
const http = createServer(async (req, res) => {
  if (!req.url.startsWith("/api/")) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end('<html><head><title>Test paper</title><meta name="citation_doi" content="10.1234/testing"></head><body>Paper</body></html>');
    return;
  }
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/session") res.end('{"user":"alice"}');
  else if (req.url === "/api/library/folders") res.end(JSON.stringify({ folders, labels: [{ id: "l-1", name: "physics" }] }));
  else if (req.url === "/api/clip") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const clip = JSON.parse(body);
    // The server makes a folder named by path (a "New folder…").
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
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  // A default folder as an older version stored it: a path.
  await worker.evaluate((server) => chrome.storage.sync.set({ server, folder: "Reading/2026" }), origin);
  const sync = () => worker.evaluate(() => chrome.storage.sync.get(["defaultFolders", "folder"]));
  const id = new URL(worker.url()).host;
  const errors = [];

  // Options: the stored path shows as its folder; a pick stores the id.
  const options = await context.newPage();
  options.on("pageerror", (error) => errors.push(error.message));
  await options.goto(`chrome-extension://${id}/options.html`);
  await options.waitForFunction(() => !document.getElementById("folder-btn").disabled);
  assert.equal(await options.locator("#folder-btn").textContent(), "Reading / 2026");
  await options.locator("#folder-btn").click();
  assert.deepEqual(await options.locator("#folder-menu .ctxMenuText").allTextContents(), ["Library root", "Reading / 2026", "Papers"]);
  await options.locator("#folder-menu .ctxMenuItem", { hasText: "Papers" }).click();
  await options.waitForFunction(() => document.getElementById("save-status").textContent === "Saved.");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-papers" }, folder: "" });

  const paper = await context.newPage();
  await paper.goto(`${origin}/doi/10.1234/testing`);
  const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, paper.url());
  // Opens the popup on the paper, waits for the picker to show `shown` (the
  // default folder), lets `pickFolder` choose, saves, and answers the clip.
  async function popupSave(shown, pickFolder) {
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.waitForFunction((shown) => document.getElementById("folder-btn").title === shown, shown);
    // Saved once already: "Add to another folder…" brings the form back.
    if (await popup.locator("#existing").isVisible()) await popup.locator("#refile").click();
    await pickFolder(popup);
    const done = new Promise((resolve) => { clipped = resolve; });
    await popup.locator("#save").click();
    await done;
    await popup.locator("#result.ok").waitFor();
    await popup.close();
    return clips.at(-1);
  }

  // The popup starts at the default, lists every folder by path, sends the
  // picked one's id and label names (suggested from the {id, name} labels).
  let clip = await popupSave("Papers", async (popup) => {
    await popup.locator("#folder-btn").click();
    assert.deepEqual(await popup.locator("#folder-menu .ctxMenuText").allTextContents(),
      ["Library root", "Reading / 2026", "Papers", "New folder…"]);
    await popup.locator("#folder-menu .ctxMenuItem", { hasText: "Reading / 2026" }).click();
    await popup.locator("#labels").fill("ph");
    await popup.locator("#label-menu .ctxMenuItem", { hasText: "physics" }).click();
  });
  assert.equal(clip.folder, "f-reading");
  assert.equal(clip.folder_path, "");
  assert.deepEqual(clip.labels, ["physics"]);
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "f-reading" });

  // A typed new folder goes as folder_path and is remembered by its new id.
  clip = await popupSave("Reading / 2026", async (popup) => {
    await popup.locator("#folder-btn").click();
    await popup.locator("#folder-menu .ctxMenuItem", { hasText: "New folder…" }).click();
    await popup.locator("#folder-new").fill("Inbox/new");
  });
  assert.equal(clip.folder, "");
  assert.equal(clip.folder_path, "Inbox/new");
  assert.deepEqual((await sync()).defaultFolders, { [origin]: "f-2" });

  // The shortcut and context menu save into the default by id.
  const save = () => options.evaluate(() => chrome.runtime.sendMessage({ type: "save", candidate: {
    kind: "doi", doi: "10.1234/testing", source_url: location.href,
  } }));
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "f-2");

  // A path an older version stored is sent once, then replaced by its id.
  await worker.evaluate(() => chrome.storage.sync.set({ defaultFolders: {}, folder: "Old/path" }));
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "");
  assert.equal(clips.at(-1).folder_path, "Old/path");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-3" }, folder: "" });
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).folder, "f-3");

  // The popup shows a stored path no folder has as a prefilled new folder.
  await worker.evaluate(() => chrome.storage.sync.set({ defaultFolders: {}, folder: "Fresh" }));
  clip = await popupSave("New folder…", async (popup) => {
    assert.equal(await popup.locator("#folder-new").inputValue(), "Fresh");
  });
  assert.equal(clip.folder_path, "Fresh");
  assert.deepEqual(await sync(), { defaultFolders: { [origin]: "f-4" }, folder: "" });

  assert.deepEqual(errors, []);
  console.log("PASS: folder picker by id and path, label names, new folders, the per-server default and the stored-path fallback.");
} finally {
  await context?.close();
  http.close();
  await rm(profile, { recursive: true, force: true });
}

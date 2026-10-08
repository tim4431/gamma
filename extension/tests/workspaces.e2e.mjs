// Run with node extension/tests/workspaces.e2e.mjs (Playwright full Chromium).
// Choosing which library a save lands in: the popup and options-page
// workspace pickers over GET /api/session's `workspaces`, the
// X-Gamma-Workspace header every library request then carries, a default
// folder remembered per workspace, "already in your library" answered by
// the chosen one, and a workspace the account loses being forgotten.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

// Home is the account's default, Lab a shared library it may write to, the
// reading room one it may only read — no destination for a save.
const WORKSPACES = [
  { id: "w-home", name: "Home", role: "owner", personal: true, default: true },
  { id: "w-lab", name: "Lab", role: "editor", personal: false, default: false },
  { id: "w-room", name: "Reading room", role: "viewer", personal: false, default: false },
];
const FOLDERS = {
  "": [{ id: "f-home", path: ["Inbox"] }],
  "w-lab": [{ id: "f-lab", path: ["Lab", "2026"] }],
};
let workspaces = WORKSPACES;
let solo = false;                  // answer with one library only
const clips = [];
let clipped;

const http = createServer(async (req, res) => {
  const ws = req.headers["x-gamma-workspace"] || "";
  if (!req.url.startsWith("/api/")) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end('<html><head><title>Test paper</title><meta name="citation_doi" content="10.1234/testing"></head><body>Paper</body></html>');
    return;
  }
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/session") {
    res.end(JSON.stringify({ user: "alice", default_workspace: "w-home",
      workspaces: solo ? [WORKSPACES[0]] : workspaces }));
  } else if (req.url === "/api/library/folders") {
    res.end(JSON.stringify({ folders: FOLDERS[ws] || [], labels: [] }));
  } else if (req.url.startsWith("/api/library/lookup")) {
    // The paper sits in the Lab library only.
    if (ws === "w-lab") res.end(JSON.stringify({ block_id: "p-lab", title: "Test paper", open_url: "/?block=p-lab" }));
    else { res.writeHead(404); res.end('{"detail":"not found"}'); }
  } else if (req.url === "/api/clip") {
    let body = "";
    for await (const chunk of req) body += chunk;
    clips.push({ ws, ...JSON.parse(body) });
    res.end(JSON.stringify({ block_id: "p1", doc_id: "", title: "Test paper", existed: false,
      open_url: "/?block=p1", folders: [], labels: [] }));
    clipped?.();
  } else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
});
await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${http.address().port}`;

const profile = await mkdtemp(path.join(tmpdir(), "gamma-workspaces-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), origin);
  const sync = () => worker.evaluate(() => chrome.storage.sync.get(["workspaces", "defaultFolders"]));
  const id = new URL(worker.url()).host;
  const errors = [];

  // ---- the options page: the libraries this account can save into ----
  const options = await context.newPage();
  options.on("pageerror", (error) => errors.push(error.message));
  await options.goto(`chrome-extension://${id}/options.html`);
  await options.waitForFunction(() => !document.getElementById("workspace-btn").disabled);
  assert.equal(await options.locator("#workspace-btn").textContent(), "Home", "the account's default to begin with");
  await options.locator("#workspace-btn").click();
  assert.deepEqual(await options.locator("#workspace-menu .ctxMenuText").allTextContents(), ["Home", "Lab"],
    "a library this account may only read is no destination");
  await options.locator("#workspace-menu .ctxMenuItem", { hasText: "Lab" }).click();
  await options.waitForFunction(() => document.getElementById("save-status").textContent === "Saved.");
  assert.deepEqual((await sync()).workspaces, { [origin]: "w-lab" });
  // The folder picker followed: the Lab library's own folders.
  await options.locator("#folder-btn").click();
  assert.deepEqual(await options.locator("#folder-menu .ctxMenuText").allTextContents(), ["Library root", "Lab / 2026"]);
  await options.locator("#folder-menu .ctxMenuItem", { hasText: "Lab / 2026" }).click();
  await options.waitForFunction(() => document.getElementById("folder-btn").title === "Lab / 2026");
  assert.deepEqual((await sync()).defaultFolders, { [`${origin}#w-lab`]: "f-lab" });

  // ---- the popup: the chosen library answers "already saved?" ----
  const paper = await context.newPage();
  await paper.goto(`${origin}/doi/10.1234/testing`);
  const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, paper.url());
  const openPopup = async () => {
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.locator("#view-main").waitFor();
    return popup;
  };
  let popup = await openPopup();
  await popup.locator("#existing").waitFor();
  assert.equal(await popup.locator("#existing-where").textContent(), "Already in Lab",
    "the library holding it is named once there is more than one");
  assert.equal(await popup.locator("#refile").textContent(), "Add to another folder or library…");

  // Switching to Home looks the paper up again there — not saved.
  await popup.locator("#refile").click();
  await popup.locator("#workspace-btn").click();
  await popup.locator("#workspace-menu .ctxMenuItem", { hasText: "Home" }).click();
  await popup.locator("#found").waitFor();
  await popup.waitForFunction(() => document.getElementById("folder-btn").title === "Library root");
  assert.equal(await popup.locator("#workspace-btn").textContent(), "Home");
  assert.deepEqual((await sync()).workspaces, { [origin]: "" }, "the account's default is stored as no choice");
  await popup.locator("#folder-btn").click();
  assert.deepEqual(await popup.locator("#folder-menu .ctxMenuText").allTextContents(),
    ["Library root", "Inbox", "New folder…"], "Home's folders, not the Lab's");
  await popup.locator("#folder-menu .ctxMenuItem", { hasText: "Inbox" }).click();
  let done = new Promise((resolve) => { clipped = resolve; });
  await popup.locator("#save").click();
  await done;
  await popup.locator("#result.ok").waitFor();
  assert.deepEqual({ ws: clips.at(-1).ws, folder: clips.at(-1).folder }, { ws: "", folder: "f-home" },
    "a save into the default library names no workspace, as it always has");
  await popup.close();

  // Back to the Lab: its own remembered folder, and the header on the save.
  // The paper is in Home now, so the form (and its picker) is behind the
  // "Add to another folder or library…" link.
  popup = await openPopup();
  await popup.locator("#existing").waitFor();
  await popup.locator("#refile").click();
  await popup.locator("#workspace-btn").click();
  await popup.locator("#workspace-menu .ctxMenuItem", { hasText: "Lab" }).click();
  await popup.locator("#existing").waitFor();
  await popup.locator("#refile").click();
  await popup.waitForFunction(() => document.getElementById("folder-btn").title === "Lab / 2026");
  done = new Promise((resolve) => { clipped = resolve; });
  await popup.locator("#save").click();
  await done;
  await popup.locator("#result.ok").waitFor();
  assert.deepEqual({ ws: clips.at(-1).ws, folder: clips.at(-1).folder }, { ws: "w-lab", folder: "f-lab" });
  // The link back into the app opens that library.
  await popup.locator("#result.ok a").click();
  const opened = await context.waitForEvent("page");
  await opened.waitForLoadState("domcontentloaded");
  assert.equal(new URL(opened.url()).searchParams.get("ws"), "w-lab");
  await opened.close();
  await popup.close();

  // ---- a save without the popup (the shortcut, the context menu) ----
  const save = () => options.evaluate(() => chrome.runtime.sendMessage({ type: "save", candidate: {
    kind: "doi", doi: "10.1234/testing", source_url: location.href,
  } }));
  assert.equal((await save()).ok, true);
  assert.deepEqual({ ws: clips.at(-1).ws, folder: clips.at(-1).folder }, { ws: "w-lab", folder: "f-lab" },
    "the chosen library and its default folder");

  // ---- a library the account loses is forgotten ----
  workspaces = [WORKSPACES[0], { ...WORKSPACES[1], role: "viewer" }, WORKSPACES[2]];
  // The next session check is where the Connector notices (the popup's, or
  // this one): the answer is awaited, so the choice is already dropped.
  await options.evaluate(() => chrome.runtime.sendMessage({ type: "auth-changed" }));
  assert.deepEqual((await sync()).workspaces, { [origin]: "" });
  assert.equal((await save()).ok, true);
  assert.equal(clips.at(-1).ws, "", "saves fall back to the account's default library");

  // ---- one library is no choice: the row stays out of the way ----
  solo = true;
  workspaces = WORKSPACES;
  popup = await openPopup();
  await popup.locator("#found").waitFor();
  assert.equal(await popup.locator("#workspace-row").isVisible(), false);
  assert.equal(await popup.locator("#existing-where").textContent(), "Already in your library");
  await popup.close();
  await options.reload();
  await options.waitForFunction(() => !document.getElementById("folder-btn").disabled);
  assert.equal(await options.locator("#workspace-row").isVisible(), false);

  assert.deepEqual(errors, []);
  console.log("PASS: the workspace picker, its header, per-workspace folders and the lost-workspace fallback.");
} finally {
  await context?.close();
  http.close();
  await rm(profile, { recursive: true, force: true });
}

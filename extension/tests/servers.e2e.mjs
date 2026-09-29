// Run with node extension/tests/servers.e2e.mjs (Playwright full Chromium).
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

let releasePdf;
let pdfStarted;
const started = new Promise((resolve) => { pdfStarted = resolve; });
const writes = [];
async function server(name) {
  const state = { user: name, offline: false };
  const http = createServer(async (req, res) => {
    if (req.url === "/paper.pdf") {
      pdfStarted();
      await new Promise((resolve) => { releasePdf = resolve; });
      res.writeHead(200, { "Content-Type": "application/pdf" });
      res.end("%PDF-1.4\n" + "test PDF bytes\n".repeat(100));
      return;
    }
    if (!req.url.startsWith("/api/")) {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end('<html><head><title>Test paper</title><meta name="citation_doi" content="10.1234/testing"></head><body>Paper</body></html>');
      return;
    }
    res.setHeader("Content-Type", "application/json");
    if (state.offline) { res.writeHead(503); res.end('{"detail":"Offline for test"}'); return; }
    if (req.method === "POST") writes.push({ name, url: req.url });
    if (req.url === "/api/session") res.end(JSON.stringify({ user: state.user }));
    else if (req.url.startsWith("/api/library/lookup")) res.end(JSON.stringify({ block_id: `${name}-paper`, open_url: `/?block=${name}-paper` }));
    else if (req.url.startsWith("/api/library/preview")) res.end('{"title":"Test paper"}');
    else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  return { http, state, origin: `http://127.0.0.1:${http.address().port}` };
}

const a = await server("alice");
const b = await server("bob");
const profile = await mkdtemp(path.join(tmpdir(), "gamma-servers-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), a.origin);
  const id = new URL(worker.url()).host;
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`chrome-extension://${id}/options.html`);
  async function ready(user) {
    await page.waitForFunction((user) => !document.getElementById("connect").disabled &&
      document.getElementById("who").textContent === user, user);
  }
  await ready("alice");
  assert.deepEqual(await page.locator("#saved-server option").allTextContents(), [a.origin]);

  const paper = await context.newPage();
  await paper.goto(`${a.origin}/doi/10.1234/testing`);
  const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, paper.url());
  const getState = () => page.evaluate((tabId) => chrome.runtime.sendMessage({ type: "get-state", tabId }), tabId);
  assert.equal((await getState()).result.hit.block_id, "alice-paper");

  await page.locator("#server").fill(b.origin);
  await page.locator("#connect").click();
  await ready("bob");
  assert.deepEqual(await page.locator("#saved-server option").allTextContents(), [a.origin, b.origin]);
  assert.equal((await getState()).result.hit.block_id, "bob-paper");
  await page.locator("#saved-server").selectOption(a.origin);
  await ready("alice");
  assert.equal((await getState()).result.hit.block_id, "alice-paper");
  await page.reload();
  await ready("alice");
  assert.equal(await page.locator("#saved-server option").count(), 2);

  await page.locator("#server").fill("");
  await page.locator("#connect").click();
  assert.equal(await page.locator("#server-status").textContent(), "Enter a valid URL.");
  await page.evaluate(() => { chrome.permissions.request = async () => false; });
  await page.locator("#saved-server").selectOption(b.origin);
  await page.waitForFunction(() => document.getElementById("server-status").textContent.includes("declined"));
  assert.equal(await page.locator("#saved-server").inputValue(), a.origin);
  assert.equal((await worker.evaluate(() => chrome.storage.sync.get("server"))).server, a.origin);
  await page.reload();
  await ready("alice");

  b.state.user = null;
  await page.locator("#saved-server").selectOption(b.origin);
  await ready("");
  assert.equal(await page.locator("#signed-out").isVisible(), true);
  assert.equal(await page.locator("#signed-in").isVisible(), false);
  await page.locator("#user").fill("unsent user");
  await page.locator("#pass").fill("unsent password");
  await page.locator("#saved-server").selectOption(a.origin);
  await ready("alice");
  assert.equal(await page.locator("#pass").inputValue(), "");
  b.state.offline = true;
  await page.locator("#saved-server").selectOption(b.origin);
  await page.waitForFunction(() => !document.getElementById("connect").disabled && document.getElementById("server-status").classList.contains("err"));
  assert.equal(await page.locator("#signed-in").isVisible(), false);
  assert.equal(await page.locator("#who").textContent(), "");
  await page.locator("#saved-server").selectOption(a.origin);
  await ready("alice");

  // A browser download begun for A must not upload or save to B after a switch.
  const save = page.evaluate((url) => chrome.runtime.sendMessage({ type: "save", candidate: {
    kind: "pdf", is_pdf_tab: true, pdf_url: url, source_url: url,
  } }), `${a.origin}/paper.pdf`);
  await started;
  b.state.offline = false;
  b.state.user = "bob";
  await page.locator("#saved-server").selectOption(b.origin);
  await ready("bob");
  releasePdf();
  const result = await save;
  assert.equal(result.ok, false);
  assert.match(result.error, /server changed/);
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log("PASS: existing-server migration, add/switch/reload, account and offline states, library isolation, in-flight save isolation.");
} finally {
  releasePdf?.();
  await context?.close();
  a.http.close();
  b.http.close();
  await rm(profile, { recursive: true, force: true });
}

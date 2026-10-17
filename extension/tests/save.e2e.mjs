// Run with node extension/tests/save.e2e.mjs (Playwright full Chromium).
// A save the server can't fetch the PDF for (docs/dev/extension.md "The save
// pipeline"): the first POST /api/clip asks for the 400 (web_fallback: false),
// the browser downloads the PDF itself — here from an Atypon ePDF reader that
// advertises no PDF, whose file answers only the page's own same-origin
// fetch, as science.org's bot check does — and the clip is sent again with
// the uploaded bytes. When the browser can't either, the page is saved with
// its web source. Every step lands in the save's log, which the popup and the
// options page copy.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

async function listen(http) {
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${http.address().port}`;
}

const PDF = "%PDF-1.4\n" + "test PDF bytes\n".repeat(200);

// /doi/epdf/<doi>: the reader, naming its DOI only by its path. Its file,
// /doi/pdf/<doi>?download=true, goes to a same-origin fetch for "open" and
// to nobody for "closed"; anyone else gets the bot check's 403.
const fileAsks = [];
const publisherHttp = createServer((req, res) => {
  const reader = req.url.match(/^\/doi\/epdf\/10\.1234\/(open|closed)$/);
  if (reader) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(`<html><head><title>Paper ${reader[1]}</title></head><body><h1>Reader</h1></body></html>`);
    return;
  }
  const file = req.url.match(/^\/doi\/pdf\/10\.1234\/(open|closed)\?download=true$/);
  if (file) {
    const site = req.headers["sec-fetch-site"] || "";
    fileAsks.push({ paper: file[1], site });
    if (file[1] === "open" && site === "same-origin") {
      res.writeHead(200, { "Content-Type": "application/pdf" });
      res.end(PDF);
      return;
    }
    res.writeHead(403, { "Content-Type": "text/html" });
    res.end("<html><head><title>Just a moment...</title></head><body>checking</body></html>");
    return;
  }
  res.writeHead(404); res.end();
});
const publisher = await listen(publisherHttp);

// The Gamma server: a clip without bytes is the server's paywall 400 when
// web_fallback is off, else a page with its web source.
const clips = [];
const uploads = [];
const gammaHttp = createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  let body = Buffer.alloc(0);
  for await (const chunk of req) body = Buffer.concat([body, chunk]);
  const url = req.url.split("?")[0];
  if (url === "/api/session") res.end('{"user":"alice"}');
  else if (url === "/api/library/folders") res.end('{"folders":[],"labels":[]}');
  else if (url === "/api/uploads") {
    uploads.push(body.includes("%PDF-1.4"));
    res.end(`{"doc_id":"up${uploads.length}"}`);
  } else if (url === "/api/clip") {
    const clip = JSON.parse(body.toString());
    clips.push(clip);
    const page = { block_id: `p${clips.length}`, title: clip.title || "Paper", existed: false,
                   open_url: `/?block=p${clips.length}`, folders: [], labels: [] };
    if (clip.doc_id) res.end(JSON.stringify({ ...page, doc_id: clip.doc_id }));
    else if (clip.web_fallback === false) {
      res.writeHead(400);
      res.end('{"detail":"The publisher\'s PDF isn\'t accessible server-side (browser check)."}');
    } else res.end(JSON.stringify({ ...page, doc_id: "", note: "No PDF found — saved as a page with its web source." }));
  } else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
});
const gamma = await listen(gammaHttp);

const profile = await mkdtemp(path.join(tmpdir(), "gamma-save-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), gamma);
  const id = new URL(worker.url()).host;
  const errors = [];
  // What a page put on the clipboard (CDP grants no clipboard to an
  // extension's origin, so writeText is caught instead).
  const catchCopies = (page) => page.evaluate(() => {
    navigator.clipboard.writeText = async (text) => { window.copied = text; };
  });

  // Saves the reader page `paper` from the popup; the popup after the result.
  async function popupSave(paper) {
    const tab = await context.newPage();
    await tab.goto(`${publisher}/doi/epdf/10.1234/${paper}`);
    const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, tab.url());
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.locator("#save").waitFor();
    await popup.locator(".chip", { hasText: "DOI" }).first().waitFor();  // no PDF advertised
    await popup.locator("#save").click();
    await popup.locator("#existing:not(.hidden)").waitFor();
    await popup.locator("#copy-log:not(.hidden)").waitFor();
    return { popup, tabId };
  }

  // The browser gets the PDF the server couldn't: the 400 first, then the
  // worker's fetch refused, the page's own fetch answered, the bytes uploaded.
  const open = await popupSave("open");
  assert.match(await open.popup.locator("#existing-msg").getAttribute("class"), /\bok\b/);
  assert.equal(await open.popup.locator("#existing-msg").textContent(), "Saved to your library.");
  assert.deepEqual(clips.map((c) => [c.web_fallback, c.doc_id || ""]), [[false, ""], [true, "up1"]]);
  assert.equal(clips[0].doi, "10.1234/open");
  assert.deepEqual(uploads, [true]);
  assert.deepEqual(fileAsks.map((a) => a.site === "same-origin"), [false, true]);
  // The log says each of those steps, and the popup copies it.
  const openLog = await worker.evaluate(async (k) => (await chrome.storage.session.get(k))[k].log, `tab:${open.tabId}`);
  for (const line of [
    `tab: ${publisher}/doi/epdf/10.1234/open`,
    "detected: doi · doi 10.1234/open",
    `the browser can try: ${publisher}/doi/pdf/10.1234/open?download=true`,
    "← 400: The publisher's PDF isn't accessible server-side (browser check).",
    `worker fetch ${publisher}/doi/pdf/10.1234/open?download=true → 403 text/html`,
    `in-page fetch ${publisher}/doi/pdf/10.1234/open?download=true → 200 application/pdf`,
    "POST /api/uploads",
    "POST /api/clip with the uploaded PDF",
    "← saved: page p2 · PDF up1",
  ]) assert.ok(openLog.includes(line), `the log names "${line}":\n${openLog}`);
  await catchCopies(open.popup);
  await open.popup.locator("#copy-log").click();
  await open.popup.waitForFunction(() => document.getElementById("copy-log").textContent === "Copied");
  assert.equal(await open.popup.evaluate(() => window.copied), openLog);

  // Neither can: the page is saved with its web source, and the note says
  // the browser tried too.
  clips.length = 0;
  const closed = await popupSave("closed");
  assert.match(await closed.popup.locator("#existing-msg").getAttribute("class"), /\bwarn\b/);
  assert.match(await closed.popup.locator("#existing-msg").textContent(),
    /^Saved to your library\. No PDF found — saved as a page with its web source\. Your browser couldn't download it either: /);
  assert.deepEqual(clips.map((c) => [c.web_fallback, c.doc_id || ""]), [[false, ""], [true, ""]]);
  assert.deepEqual(uploads, [true]);

  // The options page copies both saves, newest first.
  const options = await context.newPage();
  options.on("pageerror", (error) => errors.push(error.message));
  await options.goto(`chrome-extension://${id}/options.html`);
  await catchCopies(options);
  await options.locator("#copy-logs").click();
  await options.waitForFunction(() => document.getElementById("logs-status").textContent === "Copied the last 2 saves.");
  const recent = await options.evaluate(() => window.copied);
  assert.ok(recent.indexOf("10.1234/closed") < recent.indexOf("10.1234/open") && recent.includes("10.1234/open"));

  assert.deepEqual(errors, []);
  console.log("PASS: the server's 400, the browser's own download from an Atypon reader, the web-source fallback, and the copied logs.");
} finally {
  await context?.close();
  gammaHttp.close();
  publisherHttp.close();
  await rm(profile, { recursive: true, force: true });
}

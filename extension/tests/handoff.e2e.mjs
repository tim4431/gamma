// Run with node extension/tests/handoff.e2e.mjs (Playwright full Chromium).
// Background fetches handed from the chat (docs/dev/extension.md "Fetches
// handed from the chat"): the tab opens beside the Gamma tab without taking
// its focus, a tab the user never switched to closes once its PDF is
// delivered, and one they did switch to stays open.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const STAY = "bg-stays-open", CLOSE = "bg-closes-after";

async function listen(http) {
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${http.address().port}`;
}

// A publisher whose landing page names its PDF; the PDF answers only once
// its gate is released, so the test chooses when each request settles.
const gates = {};
const pdfAsked = {};
const asked = Object.fromEntries([STAY, CLOSE].map((id) => [id, new Promise((r) => { pdfAsked[id] = r; })]));
const publisherHttp = createServer(async (req, res) => {
  const m = req.url.match(/^\/paper\/([\w-]+)(\.pdf)?$/);
  if (!m) { res.writeHead(404); res.end(); return; }
  if (m[2]) {
    pdfAsked[m[1]]?.();
    await new Promise((resolve) => { gates[m[1]] = resolve; });
    res.writeHead(200, { "Content-Type": "application/pdf" });
    res.end("%PDF-1.4\n" + "test PDF bytes\n".repeat(100));
    return;
  }
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(`<html><head><title>Paper ${m[1]}</title><meta name="citation_doi" content="10.1234/${m[1]}">` +
          `<meta name="citation_pdf_url" content="/paper/${m[1]}.pdf"></head><body>Paper</body></html>`);
});
const publisher = await listen(publisherHttp);

// The Gamma server's handoff endpoints (gamma/routers/ai.py, fetch_handoff.py)
// and an app page the bridge content script runs on.
const requests = {};
const watches = [];
const gammaHttp = createServer((req, res) => {
  if (!req.url.startsWith("/api/")) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end("<html><head><title>Gamma</title></head><body>Gamma</body></html>");
    return;
  }
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/session") { res.end('{"user":"alice"}'); return; }
  const m = req.url.match(/^\/api\/ai\/handoffs\/([\w-]+)(?:\/(watch|pdf))?$/);
  const r = m && requests[m[1]];
  if (!r) { res.writeHead(404); res.end('{"detail":"Not found"}'); return; }
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    if (m[2] === "watch") {
      const { note = "", background = false } = body.startsWith("{") ? JSON.parse(body) : {};
      watches.push({ id: r.id, note, background });
      Object.assign(r, { watched: true, note, background });
    } else if (m[2] === "pdf") {
      if (r.status !== "waiting") { res.writeHead(409); res.end('{"detail":"Settled"}'); return; }
      r.status = "done";
      res.end('{"pages":1}');
      return;
    }
    res.end(JSON.stringify(r));
  });
});
const gamma = await listen(gammaHttp);
for (const id of [STAY, CLOSE]) {
  requests[id] = { id, source: `10.1234/${id}`, url: `${publisher}/paper/${id}`, pdf_url: `${publisher}/paper/${id}.pdf`,
                   host: "127.0.0.1", wall: "paywall", detail: "", status: "waiting", created_at: "",
                   watched: false, note: "", background: false, pages: 0, held: false, from_url: "" };
}

async function until(fn, what, ms = 15000) {
  const end = Date.now() + ms;
  for (;;) {
    const got = await fn();
    if (got) return got;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

const profile = await mkdtemp(path.join(tmpdir(), "gamma-handoff-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), gamma);
  const bindings = () => worker.evaluate(async () => (await chrome.storage.session.get("handoffs")).handoffs || {});
  const tabOf = async (id) => Number(Object.entries(await bindings()).find(([, b]) => b.id === id)?.[0] ?? -1);
  const tab = (tabId) => worker.evaluate((tabId) => chrome.tabs.get(tabId).catch(() => null), tabId);

  const app = await context.newPage();
  await app.goto(`${gamma}/`);
  const appTab = (await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0], `${gamma}/`));
  assert.equal(appTab.active, true);

  // The chat card's "fetch in the background", the way the app sends it.
  const open = (id) => app.evaluate((id) => new Promise((resolve) => {
    window.addEventListener("message", function done(event) {
      const d = event.data;
      if (d && d.source === "gamma-connector" && d.type === "connector-tab" && d.id === id) {
        window.removeEventListener("message", done);
        resolve(d.status);
      }
    });
    window.postMessage({ source: "gamma-app", type: "connector-tab", id, do: "open", background: true }, location.origin);
  }), id);
  assert.equal(await open(STAY), "opened");
  assert.equal(await open(CLOSE), "opened");

  // (a) Both load as ordinary tabs beside the Gamma tab, in its window, not
  // on top of it: the Gamma tab is still the active one. (Tab activity is
  // the check; headless Chromium does not hide an inactive tab's document,
  // so visibilityState says nothing there.)
  const stayTab = await tabOf(STAY), closeTab = await tabOf(CLOSE);
  assert.ok(stayTab >= 0 && closeTab >= 0);
  for (const tabId of [stayTab, closeTab]) {
    const t = await tab(tabId);
    assert.equal(t.active, false);
    assert.equal(t.windowId, appTab.windowId);
    assert.equal(t.openerTabId, appTab.id);
  }
  assert.equal((await tab(appTab.id)).active, true);
  await Promise.all([asked[STAY], asked[CLOSE]]); // both pages loaded and their PDFs are being fetched
  const stayPage = await until(() => context.pages().find((p) => p.url() === `${publisher}/paper/${STAY}`), "the background page");
  assert.equal((await tab(appTab.id)).active, true);
  assert.deepEqual(watches.filter((w) => w.note === "").map((w) => w.background), [true, true]);

  // (b) The user switches to one of them: it is theirs from then on.
  await worker.evaluate((tabId) => chrome.tabs.update(tabId, { active: true }), stayTab);
  await until(async () => (await bindings())[stayTab]?.background === false, "the switched-to tab to become the user's");
  assert.equal((await tab(stayTab)).active, true);
  assert.equal((await tab(appTab.id)).active, false);
  assert.equal(watches.at(-1).background, false);

  // Both PDFs arrive. The untouched tab closes with its delivery; the one
  // the user looked at stays open, and the Gamma tab comes forward.
  gates[STAY](); gates[CLOSE]();
  await until(() => requests[STAY].status === "done" && requests[CLOSE].status === "done", "both deliveries");
  await until(async () => !Object.keys(await bindings()).length, "the bindings to be released");
  await until(async () => !(await tab(closeTab)), "the untouched background tab to close");
  await until(async () => (await tab(appTab.id)).active, "the Gamma tab to come forward");
  const kept = await tab(stayTab);
  assert.ok(kept, "the tab the user switched to was closed");
  assert.equal(kept.url, `${publisher}/paper/${STAY}`);
  assert.equal(stayPage.isClosed(), false);
  console.log("PASS: background handoff tabs open unfocused beside the Gamma tab; an unseen one closes on delivery, a switched-to one stays.");
} finally {
  for (const release of Object.values(gates)) release?.();
  await context?.close();
  publisherHttp.close();
  gammaHttp.close();
  await rm(profile, { recursive: true, force: true });
}

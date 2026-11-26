// Run with node extension/tests/openapp.e2e.mjs (Playwright full Chromium).
// "Open in Gamma" with a Gamma tab already open (docs/dev/extension.md
// "Opening Gamma"): the Connector hands the link to the Gamma tab used last,
// never a share view, and brings it forward. An app that answers opens the
// link in place (the app's half is the browser suite's "the Connector's Open
// in Gamma" step); one that doesn't has the tab load it. With the options
// page's switch off, a new tab opens.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import headed from "../../tools/headed.cjs";

// The Gamma server. Its app page keeps the links handed to it and answers
// as the app does (shared/lib/connector.js useConnectorOpen); `?mute` stands
// for an app from before it, which doesn't answer.
const APP = `<html><head><title>Gamma</title><script>
  window.handed = [];
  addEventListener("message", (e) => {
    const d = e.data;
    if (e.source !== window || !d || d.source !== "gamma-connector" || d.type !== "app-open") return;
    handed.push(d.url);
    if (!new URLSearchParams(location.search).has("mute")) {
      postMessage({ source: "gamma-app", type: "app-opened", id: d.id }, location.origin);
    }
  });
</script></head><body>Gamma</body></html>`;
const gammaHttp = createServer((req, res) => {
  if (!req.url.startsWith("/api/")) {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(APP);
    return;
  }
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/session") { res.end('{"user":"alice","workspaces":[]}'); return; }
  res.writeHead(404);
  res.end('{"detail":"Not found"}');
});
await new Promise((resolve) => gammaHttp.listen(0, "127.0.0.1", resolve));
const gamma = `http://127.0.0.1:${gammaHttp.address().port}`;

async function until(fn, what, ms = 10000) {
  const end = Date.now() + ms;
  for (;;) {
    const got = await fn();
    if (got) return got;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

const profile = await mkdtemp(path.join(tmpdir(), "gamma-openapp-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: !headed,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), gamma);
  const tabs = () => worker.evaluate(() => chrome.tabs.query({}));
  const tabAt = async (url) => (await tabs()).find((t) => t.url === url);

  // The Gamma tab, then a share view used after it, then the options page
  // the clicks are sent from, as the popup sends them.
  const app = await context.newPage();
  await app.goto(`${gamma}/`);
  const appTabId = (await tabAt(`${gamma}/`)).id;
  const share = await context.newPage();
  await share.goto(`${gamma}/?share=s1`);
  const options = await context.newPage();
  await options.goto(`${worker.url().replace(/worker\.js$/, "")}options.html`);
  const reuse = options.locator("#reuse-tab");
  await until(() => reuse.isChecked(), "the switch on by default");
  const open = (path) => options.evaluate((path) => chrome.runtime.sendMessage({ type: "open", path }), path);
  const count = (await tabs()).length;

  // (a) The app takes the link: it opens there, the tab stays where it is
  // and comes forward, and no tab is added. The share view hears nothing.
  assert.deepEqual(await open("/?block=p1"), { ok: true, result: true });
  assert.deepEqual(await app.evaluate(() => window.handed), [`${gamma}/?block=p1`]);
  assert.equal(app.url(), `${gamma}/`);
  assert.deepEqual(await share.evaluate(() => window.handed), []);
  assert.equal((await tabs()).length, count);
  assert.equal((await worker.evaluate((id) => chrome.tabs.get(id), appTabId)).active, true);

  // (b) An app that doesn't answer: the tab loads the link.
  await app.goto(`${gamma}/?mute`);
  await options.bringToFront();
  assert.deepEqual(await open("/?block=p2"), { ok: true, result: true });
  await until(() => app.url() === `${gamma}/?block=p2`, "the silent tab to load the link");
  assert.equal((await tabs()).length, count);
  assert.equal((await worker.evaluate((id) => chrome.tabs.get(id), appTabId)).active, true);

  // (c) Switched off on the options page: a new tab, the Gamma tab left as it is.
  await reuse.uncheck();
  await until(async () => (await worker.evaluate(() => chrome.storage.sync.get("reuseAppTab"))).reuseAppTab === false,
    "the switch to be stored");
  assert.deepEqual(await open("/?block=p3"), { ok: true, result: true });
  await until(() => tabAt(`${gamma}/?block=p3`), "a new tab on the link");
  assert.equal((await tabs()).length, count + 1);
  assert.equal(app.url(), `${gamma}/?block=p2`);
  console.log("PASS: Open in Gamma hands the link to the Gamma tab used last (in place, or loaded there when its app is silent); switched off, it opens a new tab.");
} finally {
  await context?.close();
  gammaHttp.close();
  await rm(profile, { recursive: true, force: true });
}

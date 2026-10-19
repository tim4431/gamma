// Run with node extension/tests/prerender.e2e.mjs (full Chromium).
// A page Chrome prerenders into a tab — Chrome's own search warmup
// (www.google.com/warmup.html, "Warmup Page") or a site's speculation rules —
// runs the content script while the tab still shows the paper. It must not
// replace the paper's detection nor answer the popup's questions; once the
// user navigates to it, it is the tab's page and reports itself.
// Chromium never prerenders in a tab Playwright is attached to
// (PrerenderingDisabledByDevTools), so this launches Playwright's Chromium
// itself and speaks DevTools only to the extension's worker and popup.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(what, check, ms = 10000) {
  for (const end = Date.now() + ms; Date.now() < end; await wait(100)) {
    const value = await check();
    if (value) return value;
  }
  throw new Error(`timed out waiting for ${what}`);
}

// /paper prerenders /warmup, which answers late so its content script
// reports after the paper's; the paper navigates there when the test says.
// The warmup page says when it loaded and when it was shown (`heard`).
const purposes = [];
const heard = [];
let go = false;
const http = createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/paper") {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(`<html><head><title>Test paper</title><meta name="citation_doi" content="10.1234/paper">
      <script type="speculationrules">{"prerender": [{"source": "list", "urls": ["/warmup"], "eagerness": "immediate"}]}</script>
      </head><body><h1>Paper</h1>
      <script>setInterval(async () => { if (await (await fetch("/go")).text() === "yes") location.href = "/warmup"; }, 200);</script>
      </body></html>`);
  } else if (url.pathname === "/warmup") {
    purposes.push(req.headers["sec-purpose"] || "");
    await wait(800);
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(`<html><head><title>Warmup Page</title><meta name="citation_doi" content="10.1234/warmup"></head><body>warm
      <script>
        addEventListener("load", () => fetch("/heard?loaded"));
        document.addEventListener("prerenderingchange", () => fetch("/heard?shown"));
      </script></body></html>`);
  } else if (url.pathname === "/heard") {
    heard.push(url.search.slice(1));
    res.end();
  } else if (url.pathname === "/go") {
    res.end(go ? "yes" : "no");
  } else if (url.pathname === "/api/session") {
    res.setHeader("Content-Type", "application/json");
    res.end('{"user":"alice"}');
  } else {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end('{"detail":"Not found"}');
  }
});
await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${http.address().port}`;

const profile = await mkdtemp(path.join(tmpdir(), "gamma-prerender-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
const browser = spawn(chromium.executablePath(), [
  "--headless", "--no-first-run", "--no-default-browser-check", `--user-data-dir=${profile}`,
  "--remote-debugging-port=0", `--disable-extensions-except=${extension}`, `--load-extension=${extension}`,
  "about:blank",
], { stdio: "ignore" });
const exited = new Promise((resolve) => browser.once("exit", resolve));
let ws;
try {
  const [port, wsPath] = await until("DevTools", async () => {
    try { return (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n"); } catch { return null; }
  });
  ws = new WebSocket(`ws://127.0.0.1:${port}${wsPath}`);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    const call = pending.get(msg.id);
    if (!call) return;
    pending.delete(msg.id);
    if (msg.error) call.reject(new Error(msg.error.message));
    else call.resolve(msg.result);
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  // Attaches to the first target `match` accepts; the result runs
  // fn(arg) there and answers its (awaited) value. `wake` runs once when
  // the target is slow to appear. A browser started on a busy machine
  // takes its time, so this waits 30 s.
  async function attach(match, wake) {
    const started = Date.now();
    let woken = false;
    const target = await until("a target", async () => {
      const found = (await send("Target.getTargets")).targetInfos.find(match);
      if (!found && wake && !woken && Date.now() - started > 5000) { woken = true; await wake(); }
      return found;
    }, 30000);
    const { sessionId } = await send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
    return async (fn, arg) => {
      const { result, exceptionDetails } = await send("Runtime.evaluate", {
        expression: `(${fn})(${JSON.stringify(arg)})`, awaitPromise: true, returnByValue: true,
      }, sessionId);
      if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
      return result.value;
    };
  }

  // An extension's worker stops when idle and starts again on an event: a
  // page its content script runs on sends one (/api/session, which the
  // test's tabs never look at).
  const worker = await attach((t) => t.type === "service_worker" && /^chrome-extension:\/\/\w+\/worker\.js$/.test(t.url),
    () => send("Target.createTarget", { url: `${origin}/api/session` }));
  await worker((server) => chrome.storage.sync.set({ server }), origin);
  const id = await worker(() => chrome.runtime.id);
  const tabId = await worker(async (url) => (await chrome.tabs.create({ url })).id, `${origin}/paper`);
  const stateOf = () => worker(async (k) => (await chrome.storage.session.get(k))[k] || {}, `tab:${tabId}`);

  // The warmup page is prerendered, not navigated to, and loaded; its
  // content script's document_idle comes right after.
  await until("the prerendered page to load", () => heard.includes("loaded"));
  assert.deepEqual(purposes, ["prefetch;prerender"]);
  await wait(1500);

  // The tab's detection is still the paper's, and only the paper answers.
  const st = await stateOf();
  assert.equal(st.candidate?.doi, "10.1234/paper", `the paper's detection stands: ${JSON.stringify(st.candidate)}`);
  assert.equal(st.candidate.title, "Test paper");
  for (let i = 0; i < 5; i++) {
    const answer = await worker((tabId) => chrome.tabs.sendMessage(tabId, { type: "get-detection", fresh: true }), tabId);
    assert.equal(answer.doi, "10.1234/paper", "only the shown page answers");
  }
  await worker((url) => chrome.tabs.create({ url, active: false }), `chrome-extension://${id}/popup.html?tab=${tabId}`);
  const popup = await attach((t) => t.type === "page" && t.url.includes("/popup.html"));
  const head = await until("the popup's head", () => popup(() => {
    const chip = document.querySelector("#sub .chip");
    return chip && { chip: chip.textContent, title: document.getElementById("title").textContent };
  }));
  assert.deepEqual(head, { chip: "DOI", title: "Test paper" });

  // The paper navigates to it: the prerendered page is shown and now
  // reports itself.
  go = true;
  await until("the prerendered page to be shown", () => heard.includes("shown"));
  assert.equal(purposes.length, 1, "the prerendered page was shown, not fetched again");
  const after = await until("the shown page's detection", async () => {
    const { candidate } = await stateOf();
    return candidate?.doi === "10.1234/warmup" && candidate;
  });
  assert.equal(after.title, "Warmup Page");

  console.log("PASS: a prerendered page leaves the tab's detection and the popup to the shown page, and reports itself once shown.");
} finally {
  ws?.close();
  browser.kill();
  await exited;
  http.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 5 });
}

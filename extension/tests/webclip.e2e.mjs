// Run with node extension/tests/webclip.e2e.mjs (Playwright full Chromium).
// A page that names no paper saves as a web clip (docs/dev/extension.md "Web
// clips"): the popup offers Article, Selection and Link over the folder and
// labels. Article reads the page with Readability (clip.js) — the lazy
// picture by its real address, KaTeX's TeX, a code block's language, the
// page's navigation left out — fetches the pictures in the browser and
// uploads them, and sends the article with the clip, one it can't fetch
// keeping its address; text selected when the popup opens is what it clips;
// Link sends no article, and is the way the next page clips. A page clipped
// already is looked up by its address only once the popup opens, and the
// popup's rows keep their spacing.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import headed from "../../tools/headed.cjs";

async function listen(http) {
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${http.address().port}`;
}

// A plain PNG, w × h of one colour.
function png(w, h) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(w * 3, 0x80)]);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr),
                        chunk("IDAT", zlib.deflateSync(Buffer.concat(Array(h).fill(row)))), chunk("IEND", Buffer.alloc(0))]);
}

const GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const PARA = "Optical clocks compare an atomic transition with a laser, and the best of them now agree to eighteen digits. ";
const article = (title) => `<html><head><title>${title} — Clock Blog</title>
  <meta property="og:site_name" content="Clock Blog"><meta name="author" content="Ada Lovelace"></head><body>
  <nav><a href="/">Home</a> · <a href="/about">About the blog</a> · <a href="/feed">Feed</a></nav>
  <article><h1>${title}</h1>
    <p id="keep">${PARA.repeat(3)}</p>
    <p>The fractional uncertainty is <span class="katex"><span class="katex-mathml"><math><semantics><mrow><mn>1</mn></mrow>
      <annotation encoding="application/x-tex">10^{-18}</annotation></semantics></math></span><span class="katex-html" aria-hidden="true">10−18</span></span> now. ${PARA}</p>
    <p><img src="${GIF}" data-src="/img/fig.png" alt="The clock" width="40" height="30"></p>
    <p><img src="/img/blocked.png" alt="Hotlinked"></p>
    <pre><code class="language-js">const tick = () =&gt; clock.next();</code></pre>
    <p>${PARA.repeat(2)}</p>
  </article><footer>© Clock Blog · Subscribe</footer></body></html>`;
const siteHttp = createServer((req, res) => {
  if (req.url === "/img/fig.png") { res.writeHead(200, { "Content-Type": "image/png" }); res.end(png(40, 30)); return; }
  if (req.url === "/img/blocked.png") { res.writeHead(403); res.end(); return; }
  const title = { "/post": "Counting the ticks of optical clocks", "/post2": "Selected words", "/post3": "Only the link",
                  "/post4": "Next page", "/saved": "Clipped before" }[req.url];
  res.writeHead(title ? 200 : 404, { "Content-Type": "text/html; charset=utf-8" });
  res.end(title ? article(title) : "");
});
const site = await listen(siteHttp);

const clips = [];
const uploads = [];      // the pictures POST /api/upload-image received: their type and size
const lookups = [];      // the addresses GET /api/library/lookup was asked about
const gammaHttp = createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  let body = Buffer.alloc(0);
  for await (const chunk of req) body = Buffer.concat([body, chunk]);
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/api/session") res.end('{"user":"alice"}');
  else if (url.pathname === "/api/library/folders") res.end('{"folders":[{"id":"f-web","path":["Web"]}],"labels":[]}');
  else if (url.pathname === "/api/library/lookup") {
    lookups.push(url.searchParams.get("url"));
    if (url.searchParams.get("url") === `${site}/saved`) {
      res.end(JSON.stringify({ block_id: "old", title: "Clipped before", existed: true, doc_id: "", open_url: "/?block=old", folders: [], labels: [] }));
    } else { res.writeHead(404); res.end('{"detail":"not in library"}'); }
  } else if (url.pathname === "/api/upload-image") {
    const type = /Content-Type: (\S+)/i.exec(body.toString("latin1"))?.[1] || "";
    uploads.push({ type, png: body.includes(Buffer.from([0x89, 0x50, 0x4e, 0x47])) });
    res.end(JSON.stringify({ url: `/api/uploads/pic${uploads.length}.png`, size: body.length, already_existed: false }));
  } else if (url.pathname === "/api/clip") {
    const clip = JSON.parse(body.toString());
    clips.push(clip);
    res.end(JSON.stringify({ block_id: `p${clips.length}`, title: clip.title || "Page", existed: false, doc_id: "",
                             open_url: `/?block=p${clips.length}`, folders: [], labels: [] }));
  } else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
});
const gamma = await listen(gammaHttp);

const profile = await mkdtemp(path.join(tmpdir(), "gamma-webclip-"));
const extension = fileURLToPath(new URL("../", import.meta.url));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium", headless: !headed, viewport: { width: 900, height: 900 },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await worker.evaluate((server) => chrome.storage.sync.set({ server }), gamma);
  const id = new URL(worker.url()).host;
  const errors = [];

  async function openTab(pathname) {
    const tab = await context.newPage();
    await tab.goto(`${site}${pathname}`);
    const tabId = await worker.evaluate(async (u) => (await chrome.tabs.query({ url: u }))[0].id, tab.url());
    await worker.evaluate(async (k) => {
      for (let i = 0; i < 100 && !(await chrome.storage.session.get(k))[k]?.candidate; i++) await new Promise((r) => setTimeout(r, 50));
    }, `tab:${tabId}`);
    return { tab, tabId };
  }
  async function popupOn(tabId) {
    const popup = await context.newPage();
    await popup.setViewportSize({ width: 360, height: 600 });
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.locator("#view-main:not(.hidden)").waitFor();
    return popup;
  }
  const pressed = (popup) => popup.locator("#mode [aria-pressed=true]").getAttribute("data-mode");

  // An article: Article is the way it saves, Selection waits for a selection.
  const post = await openTab("/post");
  let popup = await popupOn(post.tabId);
  await popup.locator("#mode-row:not(.hidden)").waitFor();
  assert.match(await popup.locator("#nothing-msg").textContent(), /^No paper or PDF on this page/);
  assert.equal(await pressed(popup), "article");
  assert.equal(await popup.locator('[data-mode="selection"]').isDisabled(), true);
  assert.equal(await popup.locator("#save-anyway").textContent(), "Save article");
  assert.equal(await popup.locator("#save").isVisible(), false);
  await popup.locator("#save-anyway").click();
  await popup.locator("#existing:not(.hidden)").waitFor();
  let clip = clips.at(-1);
  assert.equal(clip.article.kind, "article");
  assert.equal(clip.title, "Counting the ticks of optical clocks", "the article's title, the site's name left out");
  assert.deepEqual([clip.article.site, clip.article.byline], ["Clock Blog", "Ada Lovelace"]);
  const html = clip.article.html;
  assert.match(html, /data-gamma-math="inline" data-tex="10\^\{-18\}"/);
  assert.match(html, /<pre data-lang="js">const tick = \(\) =&gt; clock\.next\(\);<\/pre>/);
  assert.match(html, new RegExp(`src="${site}/img/fig\\.png"`), "the lazy picture by its real address");
  assert.doesNotMatch(html, /About the blog|Subscribe|katex-html/, "the page's navigation is not the article");
  assert.deepEqual(clip.article.images, { [`${site}/img/fig.png`]: "/api/uploads/pic1.png" }, "the blocked one keeps its address");
  assert.deepEqual(uploads, [{ type: "image/png", png: true }]);
  assert.equal(clip.web_fallback, true);
  assert.equal(await popup.locator("#existing-msg").textContent(), "Saved to your library as “Counting the ticks of optical clocks”.");
  const log = await worker.evaluate(async (k) => (await chrome.storage.session.get(k))[k].log, `tab:${post.tabId}`);
  assert.match(log, /article: .* of HTML, 2 pictures/);
  assert.match(log, /picture .*blocked\.png: kept its address \(HTTP 403\)/);

  // The saved page's own links sit under its button, the tab's tools a step
  // further down: two rows, not one block of four.
  const box = (sel) => popup.locator(sel).evaluate((el) => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; });
  const button = await box("#open-existing"), actions = await box(".pageActions"), tools = await box(".tools");
  assert.equal(Math.round(actions.top - button.bottom), 8);
  assert.equal(Math.round(tools.top - actions.bottom), 16);
  await popup.close();

  // Text selected on the page: Selection is ready, and clips only that.
  const second = await openTab("/post2");
  await second.tab.evaluate(() => {
    const range = document.createRange();
    range.selectNodeContents(document.getElementById("keep"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  popup = await popupOn(second.tabId);
  assert.equal(await pressed(popup), "selection");
  assert.equal(await popup.locator("#save-anyway").textContent(), "Save selection");
  await popup.locator("#save-anyway").click();
  await popup.locator("#existing:not(.hidden)").waitFor();
  clip = clips.at(-1);
  assert.equal(clip.article.kind, "selection");
  assert.equal(clip.article.html.trim(), PARA.repeat(3).trim());
  assert.equal(clip.title, "Selected words — Clock Blog", "a selection keeps the tab's title");
  await popup.close();

  // Link: no article, and the next page clips the same way.
  const third = await openTab("/post3");
  popup = await popupOn(third.tabId);
  await popup.locator('[data-mode="link"]').click();
  assert.equal(await popup.locator("#save-anyway").textContent(), "Save link");
  await popup.locator("#folder").fill("Web");
  await popup.locator("#folder").press("Enter");
  await popup.locator("#save-anyway").click();
  await popup.locator("#existing:not(.hidden)").waitFor();
  clip = clips.at(-1);
  assert.equal(clip.article, undefined);
  assert.equal(clip.folder, "f-web");
  assert.equal(await worker.evaluate(async () => (await chrome.storage.sync.get("webClip")).webClip), "link");
  await popup.close();
  const fourth = await openTab("/post4");
  popup = await popupOn(fourth.tabId);
  assert.equal(await pressed(popup), "link");
  await popup.locator('[data-mode="article"]').click();
  await popup.close();

  // A page clipped before: no look-up as it loads, one once the popup opens.
  const saved = await openTab("/saved");
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(lookups.includes(`${site}/saved`), false, "a page naming no paper is not looked up as it loads");
  popup = await popupOn(saved.tabId);
  await popup.locator("#existing:not(.hidden)").waitFor();
  assert.equal(await popup.locator("#existing-where").textContent(), "Already in your library");
  assert.equal(await popup.locator("#mode-row").isVisible(), false);
  // Filing it elsewhere clips nothing again.
  await popup.locator("#refile").click();
  assert.equal(await popup.locator("#mode-row").isVisible(), false);
  await popup.locator("#save").click();
  await popup.locator("#existing:not(.hidden)").waitFor();
  assert.equal(clips.at(-1).article, undefined);
  await popup.close();

  assert.deepEqual(errors, []);
  console.log("PASS: an article with its lazy picture, math and code clipped and its pictures uploaded, a selection, a link remembered, a page clipped before found once the popup opens, the popup's spacing.");
} finally {
  await context?.close();
  gammaHttp.close();
  siteHttp.close();
  await rm(profile, { recursive: true, force: true });
}

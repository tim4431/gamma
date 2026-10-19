// Run with node extension/tests/detect.e2e.mjs (Playwright full Chromium).
// What a page names when its tags name no DOI (docs/dev/extension.md
// "Detection"): a news story whose one DOI is in its text and its doi.org
// link is a "possible paper", which the popup heads with the paper's title
// and saves as that paper with doi_from_text, or as the web page itself on
// "Save the web page instead"; a reference list naming several DOIs names
// none; a repository's thesis page has no PDF or DOI but its citation tags,
// which "Save this URL anyway" sends as the record; and a publisher page's
// tags ride along with its clip.
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

const PAGES = {
  "/news/one": `<html><head><title>Scientists find a thing — Science News</title></head><body>
    <h1>Scientists find a thing</h1><p>The study (doi:10.5555/news.1) shows a thing.</p>
    <p><a href="https://doi.org/10.5555/NEWS.1">Read the paper</a></p></body></html>`,
  "/news/two": `<html><head><title>Another story — Science News</title></head><body>
    <p>The paper: <a href="https://doi.org/10.5555/news.1">doi.org/10.5555/news.1</a></p></body></html>`,
  "/refs": `<html><head><title>Publications</title></head><body><ol>
    <li>doi:10.5555/ref.1</li><li>doi:10.5555/ref.2</li><li>doi:10.5555/ref.3</li></ol></body></html>`,
  "/handle/42": `<html><head><title>Repository item</title>
    <meta name="citation_title" content="Trapped ions in optical cavities">
    <meta name="citation_author" content="Lovelace, Ada">
    <meta name="citation_publication_date" content="2019/05/03">
    <meta name="citation_dissertation_institution" content="Example University">
    </head><body><h1>Trapped ions in optical cavities</h1></body></html>`,
  "/article/7": `<html><head><title>An article</title>
    <meta name="citation_title" content="An article with tags">
    <meta name="citation_author" content="Babbage, Charles">
    <meta name="citation_journal_title" content="Journal of Things">
    <meta name="citation_doi" content="10.5555/article.7">
    <meta name="citation_pdf_url" content="/article/7.pdf">
    </head><body><p>Cites doi:10.5555/ref.9.</p></body></html>`,
};
const siteHttp = createServer((req, res) => {
  if (req.url === "/article/7.pdf") { res.writeHead(200, { "Content-Type": "application/pdf" }); res.end("%PDF-1.4\nbytes\n"); return; }
  const html = PAGES[req.url];
  res.writeHead(html ? 200 : 404, { "Content-Type": "text/html; charset=utf-8" });
  res.end(html || "");
});
const site = await listen(siteHttp);

const clips = [];
const gammaHttp = createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  let body = Buffer.alloc(0);
  for await (const chunk of req) body = Buffer.concat([body, chunk]);
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/api/session") res.end('{"user":"alice"}');
  else if (url.pathname === "/api/library/folders") res.end('{"folders":[],"labels":[]}');
  else if (url.pathname === "/api/library/preview" && url.searchParams.get("doi").toLowerCase() === "10.5555/news.1") {
    res.end(JSON.stringify({ title: "A thing, found", authors: ["A. Scientist"], year: "2026", venue: "Nature",
                             doi: "10.5555/news.1", arxiv_id: "", source: "doi" }));
  } else if (url.pathname === "/api/clip") {
    const clip = JSON.parse(body.toString());
    clips.push(clip);
    res.end(JSON.stringify({ block_id: `p${clips.length}`, title: clip.title || "Page", existed: false, doc_id: "",
                             open_url: `/?block=p${clips.length}`, folders: [], labels: [] }));
  } else { res.writeHead(404); res.end('{"detail":"Not found"}'); }
});
const gamma = await listen(gammaHttp);

const profile = await mkdtemp(path.join(tmpdir(), "gamma-detect-"));
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

  // The popup on a tab showing `pathname`, once the tab's detection is in.
  async function popupFor(pathname) {
    const tab = await context.newPage();
    await tab.goto(`${site}${pathname}`);
    const tabId = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, tab.url());
    await worker.evaluate(async (k) => {
      for (let i = 0; i < 100 && !(await chrome.storage.session.get(k))[k]?.candidate; i++) await new Promise((r) => setTimeout(r, 50));
    }, `tab:${tabId}`);
    const popup = await context.newPage();
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${id}/popup.html?tab=${tabId}`);
    await popup.locator("#view-main:not(.hidden)").waitFor();
    return { popup, tabId };
  }
  const candidate = (tabId) => worker.evaluate(async (k) => (await chrome.storage.session.get(k))[k].candidate, `tab:${tabId}`);

  // The news story: one DOI, in its text and its doi.org link.
  const news = await popupFor("/news/one");
  assert.equal(await news.popup.locator(".chip").first().textContent(), "possible paper");
  await news.popup.waitForFunction(() => document.getElementById("title").textContent === "A thing, found");
  assert.match(await news.popup.locator("#sub").textContent(), /doi:10\.5555\/news\.1.*A\. Scientist · 2026 · Nature/);
  assert.equal(await worker.evaluate((tabId) => chrome.action.getBadgeText({ tabId }), news.tabId), "?");
  await news.popup.locator("#save-page:not(.hidden)").waitFor();
  await news.popup.locator("#save").click();
  await news.popup.locator("#existing:not(.hidden)").waitFor();
  assert.deepEqual([clips[0].doi, clips[0].doi_from_text, clips[0].title, clips[0].web_fallback],
    ["10.5555/news.1", true, "A thing, found", true]);
  assert.deepEqual(clips[0].record, {});

  // Another story naming the same paper, saved as the story itself.
  const story = await popupFor("/news/two");
  await story.popup.locator("#save-page:not(.hidden)").click();
  await story.popup.locator("#existing:not(.hidden)").waitFor();
  assert.deepEqual([clips[1].doi, clips[1].doi_from_text, clips[1].title, clips[1].source_url],
    ["", false, "Another story — Science News", `${site}/news/two`]);
  await story.popup.waitForFunction(() => document.getElementById("title").textContent === "Another story — Science News");
  assert.ok(await story.popup.locator("#save-page").evaluate((el) => el.classList.contains("hidden")));
  assert.equal((await candidate(story.tabId)).kind, "none");  // the tab reads as the page now

  // A list of three DOIs names no paper.
  const refs = await popupFor("/refs");
  await refs.popup.locator("#nothing:not(.hidden)").waitFor();
  assert.match(await refs.popup.locator("#nothing-msg").textContent(), /^This page names 3 papers by DOI/);
  assert.deepEqual([(await candidate(refs.tabId)).kind, (await candidate(refs.tabId)).doi], ["none", ""]);

  // A thesis in a repository: no PDF, no DOI, its citation tags.
  const thesis = await popupFor("/handle/42");
  await thesis.popup.locator("#nothing:not(.hidden)").waitFor();
  assert.match(await thesis.popup.locator("#nothing-msg").textContent(), /citation tags name a work/);
  await thesis.popup.locator("#save-anyway").click();
  await thesis.popup.locator("#existing:not(.hidden)").waitFor();
  assert.deepEqual(clips[2].record, {
    title: "Trapped ions in optical cavities", authors: ["Ada Lovelace"], year: "2019", venue: "", volume: "",
    pages: "", publisher: "Example University", isbn: "", kind: "thesis",
  });

  // A publisher page: its tags ride along, its DOI is its own, the DOI it cites is not taken.
  const article = await popupFor("/article/7");
  await article.popup.locator("#save").click();
  await article.popup.locator("#existing:not(.hidden)").waitFor();
  const clip = clips.at(-1);
  assert.deepEqual([clip.doi, clip.doi_from_text, clip.record.kind, clip.record.venue, clip.record.authors],
    ["10.5555/article.7", false, "journalArticle", "Journal of Things", ["Charles Babbage"]]);

  assert.deepEqual(errors, []);
  console.log("PASS: a story's one DOI, saving the story instead, a list of DOIs, a thesis page's tags, a publisher page's record.");
} finally {
  await context?.close();
  gammaHttp.close();
  siteHttp.close();
  await rm(profile, { recursive: true, force: true });
}

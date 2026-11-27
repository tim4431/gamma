// Citations (docs/dev/citations.md) as a user meets them: the Export
// dialog's Bibliography card in each of its formats (a server file per
// format, downloaded as the review read it, or a reference list
// rendered in the browser in a citation style, downloaded and copied rich),
// the share popover's Reference in the account's style and its Copy as ▾,
// the library's Copy bibliography of a selection in the list's order (a
// page with nothing to cite skipped, the status pill says so), a PDF
// passage's Copy with citation at its printed page, and Settings →
// Citations, which adds a style from a .csl file, renders it, and says the
// repository is off (the suite's server downloads nothing: harness.mjs
// OFFLINE). Records are seeded through the API as a lookup leaves them.
//
// The copies are read back from the clipboard in Chromium. WebKit grants no
// clipboard permission, so there (GAMMA_E2E_BROWSER=webkit, the copy steps:
// --only "citations: copy") a stand-in clipboard keeps what the app writes
// (context() below), and a step says so in its result line.
import fs from "node:fs";
import { Account, live, settingsPane } from "../harness.mjs";
import { selectPdfText, waitForPdf } from "./pdf.mjs";

// Windows clipboards hand back CRLF; compare against the text as written.
const CRLF = String.fromCharCode(13, 10);
const LF = String.fromCharCode(10);
const plain = (text) => String(text || "").split(CRLF).join(LF).trim();
const words = (text) => String(text || "").replace(/\s+/g, " ").trim();

// The seeded journal article, and what the bundled styles make of it
// (citations/styles/apa.csl, ieee.csl over its CSL item).
const ARTICLE = { title: "Quantum tests in Rydberg arrays", authors: ["Ada Writer", "Ben Reader"], year: "2026",
  venue: "Journal of Tests", volume: "12", issue: "3", pages: "45-67", doi: "10.1234/e2e.cite", kind: "journalArticle" };
const ARTICLE_APA = "Writer, A., & Reader, B. (2026). Quantum tests in Rydberg arrays. Journal of Tests, 12(3), 45–67. https://doi.org/10.1234/e2e.cite";
const ARTICLE_IEEE = "A. Writer and B. Reader, “Quantum tests in Rydberg arrays,” Journal of Tests, vol. 12, no. 3, pp. 45–67, 2026, doi: 10.1234/e2e.cite.";
const BOOK = { title: "Passages from the life of a philosopher", authors: ["Charles Babbage"], year: "1864", kind: "book" };
const BOOK_IEEE = "C. Babbage, Passages from the life of a philosopher. 1864.";
// The PDF's paper: its record's pages run as long as the PDF (two), so a
// passage on the PDF's second page is cited at the printed page 348.
const MEMOIR = { title: "Notes on the analytical engine", authors: ["Ada Lovelace"], year: "1843", venue: "Scientific Memoirs",
  volume: "3", pages: "347-348", kind: "journalArticle" };
const MEMOIR_IEEE = "A. Lovelace, “Notes on the analytical engine,” Scientific Memoirs, vol. 3, pp. 347–348, 1843.";
const IEEE = "IEEE Reference Guide version 11.29.2023";

// A small independent style of the account's own (Settings → Citations ›
// Add from file…): a bibliography no bundled style would print.
const OWN_STYLE = `<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0">
  <info>
    <title>E2E Plain Test</title>
    <id>http://www.zotero.org/styles/e2e-plain-test</id>
    <link href="http://www.zotero.org/styles/e2e-plain-test" rel="self"/>
    <category citation-format="author-date"/>
    <updated>2026-11-25T00:00:00+00:00</updated>
  </info>
  <citation>
    <layout prefix="{" suffix="}" delimiter="; ">
      <names variable="author"><name form="short"/></names>
      <date variable="issued" prefix=" "><date-part name="year"/></date>
    </layout>
  </citation>
  <bibliography>
    <layout suffix=".">
      <text value="E2E » "/>
      <names variable="author"><name name-as-sort-order="all"/></names>
      <text variable="title" prefix=": "/>
    </layout>
  </bibliography>
</style>
`;

export async function citationScenarios({ server, browser, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  await server.manage("create-user", "cite-user", "cite-pw");
  const user = await new Account(server, "cite-user", "cite-pw").login();
  const chromium = browser.browserType().name() === "chromium";
  const folder = "E2E citations";

  // Three papers and a note page in one folder; the third paper is a PDF.
  async function paper(title, meta) {
    const made = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } });
    if (meta) await user.api(`/api/blocks/${made.id}`, { method: "PUT", body: { properties: { meta: { ...meta, source: "manual" } } } });
    await user.file(made.id, { folders: [folder] });
    return made.id;
  }
  const article = await paper(ARTICLE.title, ARTICLE);
  const book = await paper(BOOK.title, BOOK);
  await paper("Citations reading list", null);
  const up = await user.upload("/api/uploads", makePdf([["Analytical engine memoir"], ["Page two says hello world"]]), "memoir.pdf", "application/pdf");
  const memoir = (await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
    body: { default_title: MEMOIR.title, source_url: up.source_url } })).id;
  await user.api(`/api/blocks/${memoir}`, { method: "PUT", body: { properties: { meta: { ...MEMOIR, source: "manual" } } } });
  await user.file(memoir, { folders: [folder] });
  const folderId = await user.folder(folder);

  // A context of the user's whose clipboard a step reads back: Chromium's
  // own, with its permissions; elsewhere a stand-in that keeps what the app
  // writes, each promised flavour as it resolves. WebKit grants no clipboard
  // permission, and on Windows its clipboard is the system's, which it
  // refused at random (NotAllowedError for a payload ready within 1 ms, the
  // same step passing on the run before) and which a run would overwrite.
  async function context() {
    const ctx = await user.context(browser, chromium ? { permissions: ["clipboard-read", "clipboard-write"] } : {});
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    if (!chromium) {
      await ctx.addInitScript(() => {
        let held = {};
        const write = async (items) => {
          const out = {};
          for (const item of items) for (const type of item.types) out[type] = await (await item.getType(type)).text();
          held = out;
        };
        Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
          write, writeText: async (text) => { held = { "text/plain": String(text) }; },
          readText: async () => held["text/plain"] || "",
        } });
        window.__heldClipboard = () => held;
      });
    }
    return ctx;
  }
  // The clipboard's flavours: {"text/plain": …, "text/html": …}.
  const clipboard = (page) => page.evaluate(async () => {
    if (window.__heldClipboard) return window.__heldClipboard();
    const out = {};
    for (const item of await navigator.clipboard.read()) {
      for (const type of item.types) out[type] = await (await item.getType(type)).text();
    }
    return out;
  });
  // A mark put on the clipboard before a copy, and the clipboard once the
  // copy replaced it: it lands after a fetch and a render.
  const MARK = "(before the copy)";
  const mark = (page) => page.evaluate((text) => navigator.clipboard.writeText(text), MARK);
  const copied = (page) => until(async () => {
    const flavours = await clipboard(page);
    return plain(flavours["text/plain"]) && flavours["text/plain"] !== MARK ? flavours : null;
  }, { what: "the copy on the clipboard" });
  const pill = (page, text) => page.locator(".statusPill", { hasText: text }).first().waitFor({ timeout: 10000 });
  // (said in a step's result line where the clipboard is the stand-in)
  const standIn = chromium ? "" : "read from a stand-in clipboard: this engine grants no clipboard permission";
  const setPrefs = (set) => user.api("/api/prefs/profile", { method: "PATCH", body: { set } });
  const prefs = async () => (await user.api("/api/prefs/profile")).value || {};

  // The Bibliography card (transfers/BibliographyReview.jsx): the
  // review's Format row switches the right pane between the formats' entries
  // of the paper picked; a server format exports as a job whose file is the
  // one the review read (and Copy all copied), with that format's next step;
  // a reference list renders in the browser in the style picked here, the
  // whole list in the pane with the picked paper's entry marked, its
  // download an .html page and its Copy all rich.
  await step("citations: Export → Bibliography shows each format's entry, exports CSL JSON as read, and formats the list in APA then IEEE to download and copy", async () => {
    const ctx = await context();
    try {
      const page = await openPage(ctx, `${server.base}/?folder=${folderId}&ws=${user.ws}`);
      await page.waitForSelector(".folderNewBtn");
      const view = page.locator('[data-popover="menu"] > button');
      await (await view.count() ? view : page.locator('[data-guide="header.account"]')).click();
      await page.getByRole("menuitem", { name: "Export…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `Export “${folder}”`, exact: true });
      const button = (name) => dialog.getByRole("button", { name, exact: true });
      await dialog.getByRole("group", { name: "Papers choices", exact: true }).waitFor();
      const read = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/bibliography");
      await button("Bibliography").click();
      await button("Next").click();
      const answer = await (await read).json();
      assertEq(answer.entries.length, 3, "three papers to cite");
      const entry = answer.entries.find((e) => e.page_id === article);
      const papers = dialog.getByRole("region", { name: "Papers", exact: true });
      const pickPaper = () => papers.getByRole("button", { name: new RegExp(ARTICLE.title) }).click();
      await pickPaper();
      const pane = (name) => dialog.getByRole("region", { name, exact: true });
      const pick = async (menu, option) => {
        await button(menu).click();
        await page.getByRole("menuitemradio", { name: option, exact: true }).click();
      };

      // Each server format: the article's entry in it, as that format's answer has it.
      const answerIn = (format) => user.api(`/api/bibliography?folder=${folderId}&format=${format}`);
      const bibtex = await pane("BibTeX entry").locator(".bibtexPreview").innerText();
      assert(bibtex.startsWith("@article{writer2026,") && plain(bibtex) === plain(entry.text), `BibTeX first, as before: ${bibtex}`);
      for (const [label, format, has] of [
        ["BibLaTeX", "biblatex", "journaltitle = {Journal of Tests}"],
        ["RIS", "ris", "TY  - JOUR"],
        ["CSL JSON", "csljson", '"container-title": "Journal of Tests"'],
      ]) {
        const want = (await answerIn(format)).entries.find((e) => e.page_id === article).text;
        await pick("Format", label);
        // (the last format's answer stays up while this one's is read)
        const preview = pane(`${label} entry`).locator(".bibtexPreview");
        await until(async () => plain(await preview.innerText().catch(() => "")) === plain(want), { what: `the ${label} entry` })
          .catch(async (e) => { throw new Error(`${e.message}; shown: ${await preview.innerText().catch(() => "")}`); });
        assert(want.includes(has) && want.includes("10.1234/e2e.cite"), `the ${label} entry has the article's fields: ${want}`);
      }

      // CSL JSON, the format picked last: Copy all is the file, and the export's download is the same file.
      await mark(page);
      await button("Copy all").click();
      const all = (await copied(page))["text/plain"];
      const request = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      const exported = page.waitForEvent("download");
      // (a failed check before they land must not leave them to reject unheard)
      for (const waiting of [request, exported]) waiting.catch(() => {});
      await button("Export").click();
      assertEq((await request).postDataJSON().mode, "csljson", "the CSL JSON job's mode");
      const json = await exported;
      assertEq(json.suggestedFilename(), `${folder}.json`);
      const written = fs.readFileSync(await json.path(), "utf-8");
      assertEq(written, (await answerIn("csljson")).text, "the CSL JSON download is the file the review read");
      assertEq(plain(all), plain(written), "Copy all copied the CSL JSON file");
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      await dialog.getByText("Give it to Pandoc with --bibliography").waitFor();

      // A reference list: the account's style (APA) first, then IEEE picked
      // here; the picked paper's entry is the one marked in it.
      await button("2. Review").click();
      await pane("CSL JSON entry").waitFor();
      await pick("Format", "Reference list");
      await pickPaper();
      const reference = pane("Reference list").locator(".bibEntryPane .csl-entry.citeMark");
      await until(async () => (await pane("Reference list").locator(".bibEntryPane .csl-entry").count()) === 3, { what: "the whole list in the pane" });
      await until(async () => words(await reference.innerText()) === ARTICLE_APA, { what: "the article's APA reference" })
        .catch(async (e) => { throw new Error(`${e.message}; shown: ${words(await reference.innerText())}`); });
      await pick("Citation style", IEEE);
      // numbered in the review's order
      const n = answer.entries.findIndex((e) => e.page_id === article) + 1;
      await until(async () => words(await reference.innerText()).replace(/^\[(\d+)\] ?/, "[$1] ") === `[${n}] ${ARTICLE_IEEE}`,
        { what: "the article's IEEE reference" })
        .catch(async (e) => { throw new Error(`${e.message}; shown: ${words(await reference.innerText())}`); });
      // Download saves an HTML page of the whole list; no job.
      const download = page.waitForEvent("download");
      download.catch(() => {});
      await until(() => button("Download").isEnabled(), { what: "Download ready" });
      await button("Download").click();
      const file = await download;
      assertEq(file.suggestedFilename(), `${folder}.html`);
      const html = fs.readFileSync(await file.path(), "utf-8");
      assert(html.startsWith("<!DOCTYPE html>") && html.includes('<meta charset="utf-8">') && html.includes(`<title>${folder}</title>`), `an HTML page: ${html.slice(0, 160)}`);
      assert(html.includes("A. Writer and B. Reader, “Quantum tests in Rydberg arrays,” <i>Journal of Tests</i>, vol. 12, no. 3, pp. 45–67, 2026"),
        `the article in IEEE: ${html}`);
      assertEq((html.match(/class="csl-entry"/g) || []).length, 3, "every paper's entry");
      await dialog.getByText("Saved to your downloads.", { exact: true }).waitFor();
      // Copy all: the list as HTML and as text.
      await mark(page);
      await button("Copy all").click();
      const flavours = await copied(page);
      const lines = plain(flavours["text/plain"]).split(LF).map(words);
      assertEq(lines.length, 3, `three references: ${flavours["text/plain"]}`);
      assertEq(lines[n - 1], `[${n}] ${ARTICLE_IEEE}`, "the article in the text flavour");
      assert(lines.includes(`[${answer.entries.findIndex((e) => e.page_id === book) + 1}] ${BOOK_IEEE}`), `the book: ${lines}`);
      assert((flavours["text/html"] || "").includes("csl-bib-body") && flavours["text/html"].includes("<i>Journal of Tests</i>"),
        `the HTML flavour: ${flavours["text/html"]}`);
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
      return standIn;
    } finally { await ctx.close(); }
  });

  // The share popover's Citation section (citations/CitationShareSection.jsx):
  // the Reference in the account's style with its in-text citation; a style
  // picked there becomes the account's (a numeric one has no in-text copy);
  // Copy as ▸ copies the page in the formats the section shows no box for.
  await step("citations: the share popover's Reference is the APA entry, IEEE when picked there, and Copy as ▸ BibLaTeX copies the entry", async () => {
    const ctx = await context();
    try {
      const page = await openPage(ctx, `${server.base}/?page=${article}&ws=${user.ws}`);
      await page.click("button[aria-label='Share']");
      const pop = page.locator(".sharePopover");
      const reference = pop.locator(".citeRefPreview");
      await until(async () => words(await reference.innerText()) === ARTICLE_APA, { what: "the APA reference" })
        .catch(async (e) => { throw new Error(`${e.message}; shown: ${words(await reference.innerText().catch(() => ""))}`); });
      assertEq(await pop.locator(".citeInTextPreview").innerText(), "(Writer & Reader, 2026)", "its in-text citation");
      await pop.getByRole("button", { name: "Citation style", exact: true }).click();
      await page.getByRole("menuitemradio", { name: IEEE, exact: true }).click();
      await until(async () => words(await reference.innerText()).replace(/^\[1\] ?/, "[1] ") === `[1] ${ARTICLE_IEEE}`, { what: "the IEEE reference" })
        .catch(async (e) => { throw new Error(`${e.message}; shown: ${words(await reference.innerText())}`); });
      assertEq(await pop.locator(".citeInText").count(), 0, "a numeric style's [1] is not offered alone");
      await until(async () => (await prefs()).citationStyle === "ieee", { what: "IEEE saved as the account's style" });
      // the pick changes the account's style, so the pill says so and offers it back
      await page.locator(".statusPill", { hasText: "Citations now use IEEE." }).getByRole("button", { name: "Undo" }).waitFor({ timeout: 10000 });

      const { entries: [entry] } = await user.api(`/api/bibliography?page_id=${article}&format=biblatex`);
      assert(entry.text.startsWith("@article{writer2026,") && entry.text.includes("number = {3}")
        && entry.text.includes("doi = {10.1234/e2e.cite}"), `the entry: ${entry.text}`);
      await mark(page);
      await pop.getByRole("button", { name: "Copy as", exact: true }).click();
      await page.getByRole("menuitem", { name: "BibLaTeX", exact: true }).click();
      await pill(page, "Copied as BibLaTeX.");
      const flavours = await copied(page);
      assertEq(plain(flavours["text/plain"]), plain(entry.text), "the BibLaTeX entry");
      assert(!("text/html" in flavours), "a file format goes as plain text");
      assertNoProblems(page);
      return standIn;
    } finally {
      await ctx.close();
      await setPrefs({ citationStyle: null }).catch(() => {});
    }
  });

  // The library's page menu over a selection (citations/CitationMenuRows.jsx):
  // Copy bibliography takes the selected papers in the order the list shows
  // them, not the order they were picked in (IEEE numbers them so), as rich
  // text and plain; a page with no record in the selection is skipped, and
  // the pill says how many were.
  await step("citations: copy — the library's Copy bibliography takes a selection in the list's order as HTML and text, skipping a page with no record", async () => {
    await setPrefs({ citationStyle: "ieee" });
    const ctx = await context();
    try {
      const page = await openPage(ctx, `${server.base}/?folder=${folderId}&ws=${user.ws}`);
      const rows = page.locator(".fileRow");
      await until(async () => (await rows.count()) === 4, { what: "the folder's four pages" });
      const titles = await rows.allInnerTexts();
      const at = (title) => titles.findIndex((text) => text.includes(title));
      const row = (title) => rows.nth(at(title));
      const [first, second] = [[ARTICLE.title, ARTICLE_IEEE], [MEMOIR.title, MEMOIR_IEEE]].sort((a, b) => at(a[0]) - at(b[0]));
      const copyBibliography = async (on) => {
        await mark(page);
        await row(on).click({ button: "right" });
        const menu = page.locator(live(".ctxMenu")).first();
        await menu.getByRole("menuitem", { name: "Copy bibliography" }).click();
      };
      const expected = `[1] ${first[1]}${LF}[2] ${second[1]}`;
      const check = async (what) => {
        const flavours = await copied(page);
        assertEq(plain(flavours["text/plain"]).split(LF).map(words).join(LF), expected, `${what}: the text, in the list's order`);
        const html = flavours["text/html"] || "";
        const one = html.indexOf(first[1].slice(0, 20)), two = html.indexOf(second[1].slice(0, 20));
        assert(html.includes("csl-bib-body") && one >= 0 && two > one, `${what}: the HTML, in the list's order: ${html}`);
      };
      // the lower one picked first
      await row(second[0]).click();
      await row(first[0]).click({ modifiers: ["ControlOrMeta"] });
      await copyBibliography(first[0]);
      await pill(page, "Bibliography of 2 papers copied (IEEE).");
      await check("two papers");
      // a page with nothing to cite joins the selection: left out, and said
      await row("Citations reading list").click({ modifiers: ["ControlOrMeta"] });
      await copyBibliography(first[0]);
      await pill(page, "Bibliography of 2 papers copied (IEEE). 1 of 3 pages has no paper metadata.");
      await check("with the note page");
      assertNoProblems(page);
      return standIn;
    } finally {
      await ctx.close();
      await setPrefs({ citationStyle: null }).catch(() => {});
    }
  });

  // A quote with its citation (useCitations copyQuote): the highlight's menu
  // and the selection popup copy “quote” (Author, year, p. N) in the
  // account's style (APA), at the printed page: the record's pages span the
  // PDF's two, so its first page is 347 and its second 348.
  await step("citations: copy — a PDF highlight's and a selection's Copy with citation copy the quote with its printed page", async () => {
    const quote = "Page two says hello world";
    const rect = { x1: 72, y1: 56, x2: 72 + quote.length * 10, y2: 77 };
    const highlight = await user.api("/api/blocks", { method: "POST", body: { parent_id: memoir, content: "",
      properties: { quote, color: "#ffe28fa6", pdf_position: { pageNumber: 2, width: 612, height: 792, boundingRect: rect, rects: [rect] } } } });
    const ctx = await context();
    try {
      const page = await openPage(ctx, `${server.base}/?page=${memoir}&ws=${user.ws}`);
      await waitForPdf(page, 1);
      await page.locator('[data-page="2"]').scrollIntoViewIfNeeded();
      await waitForPdf(page, 2);
      await mark(page);
      await page.locator(`[data-page="2"] [data-hl-id="${highlight.id}"]`).first().click({ button: "right" });
      const menu = page.locator(live(".ctxMenu")).first();
      const row = menu.getByRole("menuitem", { name: "Copy with citation", exact: true });
      assert((await row.getAttribute("title")).includes("APA Style 7th edition"), "its title names the style");
      await row.click();
      await pill(page, "Quote copied with (Lovelace, 1843, p. 348).");
      const flavours = await copied(page);
      assertEq(plain(flavours["text/plain"]), `“${quote}” (Lovelace, 1843, p. 348)`, "the highlight's quote and citation");
      assert((flavours["text/html"] || "").includes("(Lovelace, 1843, p. 348)"), `the HTML flavour: ${flavours["text/html"]}`);
      // the selection popup's, on the PDF's first page
      await page.locator('[data-page="1"]').scrollIntoViewIfNeeded();
      await mark(page);
      await selectPdfText(page, 1, "engine memoir");
      await page.locator(".plainTip").getByRole("button", { name: "Copy with citation", exact: true }).click();
      assertEq(plain((await copied(page))["text/plain"]), "“engine memoir” (Lovelace, 1843, p. 347)", "the selection's quote and citation");
      assertNoProblems(page);
      return standIn;
    } finally {
      await ctx.close();
      await user.api(`/api/blocks/${highlight.id}`, { method: "DELETE" }).catch(() => {});
    }
  });

  // Settings → Citations (settings/SettingsCitations.jsx): Add style… says
  // the repository is off on this server and keeps Add from file…; a .csl
  // file joins Your styles and the Style menu, the preview renders the
  // sample in it, and removing the style in use puts APA back.
  await step("citations: Settings → Citations adds a style from a .csl file and previews it; the repository says downloads are off", async () => {
    const ctx = await context();
    try {
      const page = await openPage(ctx, server.base);
      await page.waitForSelector(".folderNewBtn");
      await page.locator('[data-guide="header.account"]').click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await settingsPane(page, "Citations");
      const pane = page.locator(".settingsPane");
      const preview = pane.getByRole("group", { name: "Preview of the style", exact: true });
      await until(async () => words(await preview.innerText()).includes("Einstein, A. (1905). Zur Elektrodynamik bewegter Körper."),
        { what: "the sample in APA" }).catch(async (e) => { throw new Error(`${e.message}; shown: ${await preview.innerText()}`); });

      const add = pane.getByRole("button", { name: "Add style…", exact: true });
      await until(() => add.isEnabled(), { what: "Add style… ready" });
      await add.click();
      const dialog = page.getByRole("dialog", { name: "Add a citation style", exact: true });
      await dialog.getByText("Downloading citation styles is switched off on this server. A style's .csl file can still be added.").waitFor();
      assertEq(await dialog.getByRole("searchbox", { name: "Search citation styles" }).count(), 0, "no search to make");
      const chooser = page.waitForEvent("filechooser");
      await dialog.getByRole("button", { name: "Add from file…", exact: true }).click();
      await (await chooser).setFiles({ name: "e2e-plain-test.csl", mimeType: "application/xml", buffer: Buffer.from(OWN_STYLE) });
      await dialog.waitFor({ state: "detached" });
      await pill(page, "Added E2E Plain Test.");
      const mine = pane.locator('.settingRow[data-setting="E2E Plain Test"]');
      await mine.getByText("Author-date · from a .csl file", { exact: true }).waitFor();

      // Picked in the Style menu, it is the account's, and the preview is in it.
      await pane.getByRole("button", { name: "Citation style", exact: true }).click();
      await page.getByRole("menuitemradio", { name: "E2E Plain Test", exact: true }).click();
      await until(async () => words(await preview.innerText()).includes("E2E » Einstein, Albert: Zur Elektrodynamik bewegter Körper."),
        { what: "the sample in the added style" }).catch(async (e) => { throw new Error(`${e.message}; shown: ${await preview.innerText()}`); });
      assert(words(await preview.innerText()).includes("{Einstein 1905;"), `its in-text citation: ${await preview.innerText()}`);
      await until(async () => (await prefs()).citationStyle === "e2e-plain-test", { what: "the style saved as the account's" });
      await mine.getByText("In use", { exact: true }).waitFor();

      // Removed while in use: APA again.
      await mine.getByRole("button", { name: "Remove E2E Plain Test", exact: true }).click();
      await pill(page, "Removed E2E Plain Test. Citations use APA Style 7th edition again.");
      await mine.waitFor({ state: "detached" });
      await until(async () => words(await preview.innerText()).includes("Einstein, A. (1905)."), { what: "the sample in APA again" });
      assertEq((await user.api("/api/citations/styles")).styles.length, 0, "the style is gone from the server");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await setPrefs({ citationStyle: null }).catch(() => {});
      await user.api("/api/citations/styles/e2e-plain-test", { method: "DELETE" }).catch(() => {});
    }
  });
}

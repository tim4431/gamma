import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, ROOT, Server, fakeAiModels, pressMouseBack, wanted } from "../harness.mjs";

// Text in search and the chat (docs/research/file-explorer.md step 4;
// docs/dev/api.md "Search", docs/dev/ai_context.md "A text file",
// docs/dev/pdf_citations.md "Line links", docs/dev/documents.md "The line
// mark"): a work folder of the SyncTeX sample paper with its .tex sources
// tracked. A search finds a file's line and opens the code view there,
// marked; a folder scope on the work folder goes to the server as its id,
// never as the pages the listing knows; a chat answer's line link opens the
// file at its line, from the chat and from a cold load, which is the plan's
// gate; a line past the end and a line moved under its link say so; an MCP
// client reads the line and finds it again after the file changed; a note
// keeps the link as a pill, whose place is spent once the reader leaves the
// file, and opens a file of a folder share at its line too; on a phone the
// pill brings the code view forward. The vaults recipe (GAMMA_FOLDER_LINKS, no shell secret, a
// desktop user agent).
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper", "v1");
// The word is on sections/method.tex's line 2 and on no other line of the sources.
const WORD = "gradient";
const LINE = "We fit a small network to the training set by gradient descent.";
// A .tex is not among what a work folder tracks with no .gammaignore (notes, PDFs, pictures).
const IGNORE = "*\n!*/\n!*.tex\n!*.pdf\n";

export async function textSearchScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("text search")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-text-search-"));
  try {
    await server.start();
    await server.manage("create-user", "text-user", "text-pw");
    const user = await new Account(server, "text-user", "text-pw").login();
    const home = `${server.base}/?ws=${user.ws}`;
    const at = (id, link = "") => `${server.base}/?page=${id}${link}&ws=${user.ws}`;
    // A desktop app's window with a chat model to talk to (its answers are each step's own).
    const desktop = async (opts = {}) => {
      const ctx = await user.context(browser, { userAgent: DESKTOP_UA, ...opts });
      await fakeAiModels(ctx);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      return ctx;
    };
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    const paperDir = path.join(disk, "paper");
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      write(path.join(paperDir, rel), fs.readFileSync(path.join(PAPER, rel)));
    }
    write(path.join(paperDir, ".gammaignore"), IGNORE);

    const pagesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const fileNamed = (pages, name) => pages.find((p) => p.properties?.file_name === name);
    // every folder of the listing's tree, at any depth
    const allFolders = async () => (await user.api("/api/blocks/root/children")).folders.children
      .flatMap(function all(f) { return [f, ...(f.children || []).flatMap(all)]; });
    const docOf = async (id) => (await user.api(`/api/blocks/${id}`)).properties?.doc || null;
    const blockOf = (page) => new URL(page.url()).searchParams.get("block");
    // The code view's marked lines and the sentence that says where they are.
    const mark = (page) => page.locator(".docTextView .cm-line.docLineMark");
    const notice = (page) => page.locator(".docTextView .docMarkNotice");
    const marked = (page, what = `the line holding "${WORD} descent" marked`) => until(async () => {
      const lines = await mark(page).allInnerTexts();
      return lines.length === 1 && lines[0].includes(`${WORD} descent`);
    }, { timeout: 20000, what });
    // The marked line's number: its place among the code view's lines (a short file draws them all).
    const markedLine = (page) => page.evaluate(() => [...document.querySelectorAll(".docTextView .cm-content > .cm-line")]
      .findIndex((el) => el.classList.contains("docLineMark")) + 1);
    const onScreen = async (page, what) => {
      const box = await mark(page).boundingBox();
      const view = page.viewportSize();
      assert(box && box.y >= 0 && box.y + box.height <= view.height && box.x >= 0 && box.x < view.width,
        `${what}: ${JSON.stringify(box)} in ${JSON.stringify(view)}`);
    };
    // The search panel over the library (Ctrl+Shift+F), its details shown, and its box.
    const openSearch = async (page) => {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      await page.evaluate(() => document.activeElement?.blur?.());
      await page.keyboard.press("ControlOrMeta+Shift+F");
      const search = page.locator(".searchPopover .searchInput");
      await search.waitFor();
      const details = page.locator("button[aria-label='Toggle result details']");
      if (!/\bon\b/.test(await details.getAttribute("class"))) await details.click();
      return search;
    };
    // A file's hit in the panel's Files group (the last group: every row after its heading).
    const fileHit = (page, title) => page.locator('.searchPopover .searchSection:has-text("Files") ~ .searchResult')
      .filter({ has: page.locator(".searchResultTitle", { hasText: title }) });

    let link = null, W = "", main = null, method = null;
    await step("text search: a work folder of the paper tracks its .tex sources as text pages, read where they are", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      W = link.folder_id;
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      main = await until(async () => fileNamed(await pagesOf(W), "main.tex"), { timeout: 30000, what: "main.tex's page" });
      const sections = await until(async () => (await allFolders()).find((f) => f.content === "sections"), { what: "the sections directory's folder" });
      method = await until(async () => fileNamed(await pagesOf(sections.id), "method.tex"), { timeout: 30000, what: "sections/method.tex's page" });
      for (const p of [main, method]) {
        const doc = p.properties.doc;
        assertEq(JSON.stringify([doc?.format, doc?.ext, doc?.at]), JSON.stringify(["text", ".tex", "path"]), `${p.content}: a LaTeX file read where it is`);
      }
      assertEq(method.content, "method.tex", "a file's page is titled by its name");
    });

    await step("text search: a file's line is a hit under Files, with its directory and line, and opens the code view there, marked", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, home);
      try {
        const search = await openSearch(page);
        await search.fill(WORD);
        const hit = fileHit(page, "method.tex");
        // The first search starts the index and answers before it is done
        // (the panel says it is indexing): the question is asked again, its
        // last letter off and on, since the index takes a last word as a prefix.
        let asked = Date.now(), query = WORD;
        await until(async () => {
          if (await hit.count()) return true;
          if (Date.now() - asked > 2000) {
            asked = Date.now();
            query = query === WORD ? WORD.slice(0, -1) : WORD;
            await search.fill(query);
          }
          return false;
        }, { timeout: 30000, every: 250, what: "method.tex's hit under Files" });
        assertEq(await hit.count(), 1, "one hit: the word is on one line");
        assert((await hit.locator(".searchResultPath").innerText()).includes("sections"), "the hit names the file's directory");
        assertEq((await hit.locator(".searchPageTag").innerText()).trim(), "line 2", "and its line");
        assert((await hit.locator(".searchResultText").innerText()).includes(`${WORD} descent`), "the line is the hit's text");
        await hit.click();
        await page.locator(".docTextView").waitFor({ timeout: 20000 });
        await marked(page);
        await until(() => blockOf(page) === method.id, { what: "the address names method.tex's page" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: a folder scope on the work folder goes to the server as its id, and reaches a file the listing never carries", async () => {
      const listed = (await user.api("/api/blocks/root/children")).children.map((p) => p.id);
      assert(!listed.includes(method.id), "the library's listing leaves the work folder's files out");
      const ctx = await desktop();
      const page = await openPage(ctx, home);
      try {
        const search = await openSearch(page);
        const asked = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/document-search" && r.method() === "POST"
          && r.postDataJSON()?.q === WORD && Array.isArray(r.postDataJSON()?.folders));
        await search.fill(`folder:paper ${WORD}`);
        const body = (await asked).postDataJSON();
        assertEq(JSON.stringify(body.folders), JSON.stringify([W]), "the work folder, as one id");
        assert(!("pages" in body), `no page ids travel: ${JSON.stringify(Object.keys(body))}`);
        await fileHit(page, "method.tex").waitFor({ timeout: 15000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: a chat answer's line link is a pill that opens the file at its line, marked on screen, and the link cold-loaded marks it too", async () => {
      const ctx = await desktop();
      const href = `/?page=${method.id}&line=2&quote=${encodeURIComponent(LINE)}`;
      await ctx.route("**/api/ai/chat", (route) => route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ delta: `The method is stated in [method.tex:2](${href}).` })}\n` }));
      const page = await openPage(ctx, at(main.id));
      try {
        const input = page.getByRole("combobox", { name: "Message AI" });
        await input.waitFor({ timeout: 15000 });
        await page.waitForLoadState("networkidle");
        await input.fill("Where is the method stated?");
        await input.press("Enter");
        const pill = page.locator(".chatPanel a.gammaLink-line", { hasText: "method.tex:2" });
        await pill.waitFor({ timeout: 15000 });
        assert((await pill.getAttribute("class")).includes("citePill"), "drawn as the citation's pill");
        await pill.hover();
        const preview = page.locator(".citePreview");
        await preview.waitFor();
        assert((await preview.innerText()).includes("Line 2"), `the preview names the line: ${await preview.innerText()}`);
        await page.mouse.move(0, 0);
        await preview.waitFor({ state: "detached" });
        await pill.click();
        // the Notes window is folded under the chat, so its title is read as text, not as what is drawn
        await until(async () => (await page.locator(".titleText").first().textContent()).trim() === "method.tex", { timeout: 15000, what: "method.tex opens" });
        await page.locator(".docTextView").waitFor({ timeout: 20000 });
        await marked(page);
        await onScreen(page, "the marked line is in view");
        await until(() => blockOf(page) === method.id, { what: "the address names method.tex's page" });
        assertNoProblems(page);

        await page.goto(at(method.id, "&line=2"));
        await marked(page, "the cold-loaded link marks the line");
        assertEq(await markedLine(page), 2, "line 2");
        await onScreen(page, "the cold-loaded mark is in view");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: a line past the end says so and marks nothing; after the file changes, the link's quote finds its line where it moved", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(method.id, "&line=9999"));
      try {
        await page.locator(".docTextView").waitFor({ timeout: 20000 });
        await until(async () => (await notice(page).innerText()).includes("past its end"), { timeout: 20000, what: "the notice says the line is past the end" });
        assertEq(await mark(page).count(), 0, "nothing marked");
        assertNoProblems(page);

        const before = (await docOf(method.id)).hash;
        const source = path.join(paperDir, "sections", "method.tex");
        write(source, `% The method, as the second draft has it.\n% (two lines put above the section)\n${fs.readFileSync(path.join(PAPER, "sections", "method.tex"), "utf8")}`);
        await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
        await until(async () => { const d = await docOf(method.id); return d?.hash && d.hash !== before; }, { timeout: 30000, what: "the page's document is the file's new version" });
        await page.goto(at(method.id, `&line=2&quote=${encodeURIComponent(LINE)}`));
        await marked(page, "the line marked where its text is now");
        assertEq(await markedLine(page), 4, "the two new lines moved it to line 4");
        await until(async () => (await notice(page).innerText()).includes("moved"), { what: "the notice says the line moved" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: an MCP client reads the line link, and search_library names the file's line once its new version is indexed", async () => {
      const credential = await user.api("/api/integrations/tokens", { method: "POST", body: { name: "Text search test" } });
      const call = async (name, args) => {
        const response = await fetch(`${server.base}/mcp`, { method: "POST", headers: {
          Authorization: `Bearer ${credential.token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream",
        }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
        assertEq(response.status, 200, `${name} answers`);
        const { result } = await response.json();
        assert(!result.isError, JSON.stringify(result));
        return result;
      };
      try {
        const read = await call("read_gamma_link", { url: `${server.base}/?ws=${user.ws}&page=${method.id}&line=4` });
        assertEq(read.structuredContent.line, 4, "the reference names the line");
        const text = read.content[0].text;
        assert(text.includes(`${WORD} descent`), "the read starts at the line");
        assert(text.includes("&line="), "and names its line links");
        // The change purged the old version's rows: a search starts the new
        // one's index and may answer "not indexed yet" first.
        await until(async () => (await call("search_library", { query: WORD })).content[0].text.includes('file "sections/method.tex" l.4'),
          { timeout: 30000, every: 1000, what: "search_library's file hit at line 4" });
      } finally {
        await user.api(`/api/integrations/tokens/${credential.id}`, { method: "DELETE" });
      }
    });

    await step("text search: a line link in a note is a pill, not a link chip or a card, and opens the file at its line", async () => {
      await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: `See [method.tex:4](/?page=${method.id}&line=4)` } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        const folded = page.locator(".dockWindow.collapsed", { hasText: "Notes" });
        if (await folded.count()) await folded.click();
        const pill = page.locator(".blockRow a.gammaLink-line", { hasText: "method.tex:4" });
        await pill.waitFor({ timeout: 15000 });
        assertEq(await page.locator(".blockRow a.citePill").count(), 1, "the note's link is a pill");
        assertEq(await page.locator(".blockRow a.linkChip").count(), 0, "a Gamma link is never an external link chip");
        assertEq(await page.locator(".blockRow a.gammaLinkCard").count(), 0, "a line link is not a card");
        await pill.click();
        await until(() => blockOf(page) === method.id, { what: "method.tex opens" });
        await marked(page);
        assertEq(await markedLine(page), 4, "line 4");
        assertNoProblems(page);

        // Left by the mouse's back button (no press on the page, no Escape),
        // the link's place is spent: its tab opens the file unmarked.
        await pressMouseBack(page);
        await until(() => blockOf(page) === main.id, { what: "Back returns to main.tex" });
        await page.locator(".tabStrip .tab[role=tab]", { hasText: "method.tex" }).first().click();
        await until(() => blockOf(page) === method.id, { what: "method.tex's tab opens it" });
        await until(async () => (await page.locator(".docTextView .cm-line").count()) > 2, { timeout: 20000, what: "its text is in" });
        await page.waitForTimeout(800);
        assertEq(await mark(page).count(), 0, "nothing marked");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: in a folder share, a note's line pill opens the file it names, at its line", async () => {
      const share = await user.api(`/api/share/folder/${W}`, { method: "POST", body: {} });
      const ctx = await browser.newContext();
      const page = await openPage(ctx, `${server.base}/?share=${share.token}&page=${main.id}`);
      try {
        const folded = page.locator(".dockWindow.collapsed", { hasText: "Notes" });
        await page.locator(".blockRow a.gammaLink-line, .dockWindow.collapsed").first().waitFor({ timeout: 20000 });
        if (await folded.count()) await folded.click();
        const pill = page.locator(".blockRow a.gammaLink-line", { hasText: "method.tex:4" });
        await pill.waitFor({ timeout: 15000 });
        await pill.click();
        await until(() => new URL(page.url()).searchParams.get("page") === method.id, { timeout: 15000, what: "the share opens method.tex" });
        await page.locator(".docTextView").waitFor({ timeout: 20000 });
        await marked(page);
        assertEq(await markedLine(page), 4, "line 4");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text search: on a phone the note's pill brings the code view forward with the marked line on screen, and nothing scrolls sideways", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await page.locator(".docTextView").waitFor({ timeout: 20000 });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator(".app.phoneUI .phoneBottomBar").waitFor();
        await page.locator('.phoneTabBar .phoneTab[aria-label="Notes"]').click();
        const pill = page.locator(".blockRow a.gammaLink-line:visible", { hasText: "method.tex:4" });
        await pill.waitFor({ timeout: 15000 });
        await pill.click();
        await until(() => blockOf(page) === method.id, { what: "method.tex opens" });
        await page.locator(".docTextView:visible").waitFor({ timeout: 20000 });
        await marked(page);
        await onScreen(page, "the marked line is on the phone's screen");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no sideways scroll");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

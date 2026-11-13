import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { contextOf } from "../../../src/documents/viewers/textAnchors.js";
import { Account, ROOT, Server, assert, fakeAiModels, until, wanted } from "../harness.mjs";
import { movePast } from "./pdf.mjs";

// Anchors in a text file (docs/research/file-explorer.md step 6;
// docs/dev/api.md "The highlight shape", docs/dev/highlight_relocation.md
// "Text files", docs/dev/feedback.md, docs/dev/documents.md "Viewers",
// docs/dev/comments.md "The margin"): a work folder of the SyncTeX sample
// paper with its .tex sources tracked. A selection in main.tex's code view
// makes a highlight (its lines, columns, context and the file's version)
// and a thread, whose card stands beside the text level with its line and
// whose entry reaches open.json with the file's own lines. Ten threads on
// whole lines, then the file rewritten under them (the plan's gate): the
// ones whose text survived move to their new lines, two reworded lines are
// dashed with the text they were made on, two gone are lost and kept, drawn
// as tags where they were, in the margin, the Comments window and the
// feedback files; a lost one takes its closest match by hand and is
// accepted. A selection reaches the chat by its line and file; on a phone
// the popup comments on a selection in the popover and the badge opens it.
// The vaults recipe (GAMMA_FOLDER_LINKS, no shell secret, a desktop user
// agent).
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper", "v1");
// A .tex is not among what a work folder tracks with no .gammaignore (notes, PDFs, pictures).
const IGNORE = "*\n!*/\n!*.tex\n!*.pdf\n";
// The code view's own text: the comment fields in its column are CodeMirror views too.
const CODE = ".docTextView .docTextHost > .cm-editor > .cm-scroller > .cm-content";
const YELLOW = "#ffe28fa6";

// main.tex's lines as the server decodes them.
const LINES = fs.readFileSync(path.join(PAPER, "main.tex"), "utf8").replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n");
// The whole lines the ten threads are on, and where the rewrite leaves
// each: three lines in above line 13 move all to line 46 by +3; lines 46–49
// deleted move what follows by −4 more; 23 and 36 reworded (placed roughly),
// 47 deleted and 55 replaced (lost).
const TEN = [14, 16, 22, 23, 30, 36, 41, 47, 52, 55];
const NOW = { 14: 17, 16: 19, 22: 25, 23: 26, 30: 33, 36: 39, 41: 44, 52: 51 };
const ROUGH = [23, 36], LOST = [47, 55];

// main.tex rewritten as an agent would: a comment and two blank lines put
// in after line 12, the caption and the grid's range reworded lightly, the
// "A last run" paragraph (46–49) deleted and the last sentence (55–56)
// replaced by one with nothing of it left but "is to".
function rewritten(lines) {
  const v2 = [...lines];
  const reword = (i, from, to) => {
    assert(v2[i].includes(from), `line ${i + 1} holds "${from}"`);
    v2[i] = v2[i].replace(from, to);
  };
  v2.splice(54, 2, "is to repeat the study on a model whose layers share one weight matrix.");
  v2.splice(45, 4);
  reword(35, "between one thousandth and one tenth", "between one ten-thousandth and one tenth");
  reword(22, "without weight decay", "without any weight decay");
  v2.splice(12, 0, "% revised", "", "");
  return `${v2.join("\n")}\n`;
}

// In the page: the code view's line holding `needle`, brought 40 px clear of
// the scroller's edges (Chromium's autoscroll belt), a Range over the
// needle's text (its text nodes split by the syntax colours and the marks),
// with `select` the document's selection set to it; the points the range
// starts and ends at.
function codeNeedle([code, needle, select]) {
  const line = [...document.querySelectorAll(`${code} > .cm-line`)].find((l) => l.textContent.includes(needle));
  if (!line) return null;
  const view = line.closest(".cm-scroller").getBoundingClientRect();
  const box = line.getBoundingClientRect();
  if (box.top - view.top < 40 || view.bottom - box.bottom < 40) line.scrollIntoView({ block: "center" });
  const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
  const nodes = [];
  let text = "";
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    nodes.push({ n, at: text.length });
    text += n.data;
  }
  const from = text.indexOf(needle), to = from + needle.length;
  const start = nodes.find((x) => from < x.at + x.n.data.length);
  const end = nodes.find((x) => to <= x.at + x.n.data.length);
  const range = document.createRange();
  range.setStart(start.n, from - start.at);
  range.setEnd(end.n, to - end.at);
  if (select) {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  }
  const rects = range.getClientRects();
  const first = rects[0], last = rects[rects.length - 1];
  return { x1: first.left + 1, y1: first.top + first.height / 2, x2: last.right - 1, y2: last.top + last.height / 2 };
}

// Select `needle` in the code view with the mouse, as dragSelectPdfText does
// on a PDF page: a drag over its text from its first character's left to its
// last one's right, measured once two readings agree (a view still
// scrolling would put the release off the text), released, then the hand
// moving on a little (`moveOn`), which opens the selection popup
// (pdf/selectionTip.js MOVE_TO_SHOW). `additive`: Ctrl held through it, as
// for a second passage in the chat. Returns the point it was released at.
export async function dragSelectCodeText(page, needle, { moveOn = true, additive = false } = {}) {
  let last = null;
  const box = await until(async () => {
    const next = await page.evaluate(codeNeedle, [CODE, needle, false]);
    if (!next) throw new Error(`text "${needle}" not found in the code view`);
    const still = last && ["x1", "y1", "x2", "y2"].every((k) => next[k] === last[k]);
    last = next;
    return still && next;
  }, { what: `"${needle}" to hold still` });
  if (additive) await page.keyboard.down("Control");
  await page.mouse.move(box.x1, box.y1);
  await page.mouse.down();
  await page.mouse.move((box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2, { steps: 4 });
  await page.mouse.move(box.x2, box.y2, { steps: 4 });
  await page.mouse.up();
  if (additive) await page.keyboard.up("Control");
  const release = { x: box.x2, y: box.y2 };
  const got = await page.evaluate(() => window.getSelection().toString().trim());
  if (got !== needle) throw new Error(`the drag over "${needle}" selected "${got}"`);
  if (moveOn) await movePast(page, release);
  return release;
}

export async function textAnchorScenarios(env) {
  const { browser, step, openPage, assertEq, assertNoProblems } = env;
  if (!wanted("text anchors")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-text-anchors-"));
  try {
    await server.start();
    await server.manage("create-user", "ta-user", "ta-pw");
    const user = await new Account(server, "ta-user", "ta-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    // A desktop app's window with a chat model to talk to.
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
    const feedbackDir = path.join(paperDir, ".gamma", "feedback");
    const openJson = () => JSON.parse(fs.readFileSync(path.join(feedbackDir, "open.json"), "utf8"));
    const openMd = () => fs.readFileSync(path.join(feedbackDir, "open.md"), "utf8");
    const filesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const threadsOf = async (page, state = "all") => (await user.api(`/api/pages/${page}/comments?state=${state}`)).threads;
    const blockOf = async (page, id) => {
      const walk = (b) => { if (b.id === id) return b; for (const c of b.children || []) { const f = walk(c); if (f) return f; } return null; };
      return walk((await user.api(`/api/blocks/${page}/subtree`)).block);
    };
    const docHash = async (page) => (await user.api(`/api/blocks/${page}`)).properties?.doc?.hash || "";
    let link = null;
    const sync = () => user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
    // The code view with main.tex's lines in it.
    const codeShown = (page) => until(async () => (await page.locator(`${CODE} > .cm-line`).count()) > 20,
      { timeout: 20000, what: "main.tex's lines in the code view" });
    // The ids of the ranges drawn in the text (a mark's, a lost one's tag), `extra` a class they carry.
    const drawn = (page, extra = "") => page.evaluate(([code, extra]) => [...new Set([...document.querySelectorAll(`${code} [data-hl-id]${extra}`)]
      .map((el) => el.getAttribute("data-hl-id")))].sort(), [CODE, extra]);
    const range = (page, id) => page.locator(`${CODE} [data-hl-id="${id}"]`).first();
    const column = (page) => page.locator(".docTextView .commentMargin");
    const field = (scope) => scope.locator(".commentField .cm-content");
    const focused = (f, what) => until(async () => f.evaluate((el) => el === document.activeElement).catch(() => false), { what });
    const menuItem = (page, text) => page.locator(".ctxMenu .ctxMenuItem", { hasText: text });
    const closeMenu = async (page) => {
      await page.keyboard.press("Escape");
      await page.locator(".ctxMenu").waitFor({ state: "detached" });
    };
    const showNotes = async (page) => {
      const folded = page.locator(".dockWindow.collapsed", { hasText: "Notes" });
      if (await folded.count()) await folded.click();
    };
    const openComments = async (page) => {
      const panel = page.locator(".commentsPanel");
      if (!(await panel.count())) {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
        await page.keyboard.press("Escape");
      }
      await panel.waitFor();
      return panel;
    };
    // open.json once `ready` holds of it (the hot pass writes it within seconds)
    const openJsonWhen = (ready, what) => until(() => {
      try { const d = openJson(); return ready(d) ? d : null; } catch { return null; }
    }, { timeout: 10000, what });

    let main = null, hash1 = "";
    await step("text anchors: the work folder tracks main.tex, whose page opens in the code view", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      await sync();
      main = await until(async () => (await filesOf(link.folder_id)).find((p) => p.properties?.file_name === "main.tex"),
        { timeout: 30000, what: "main.tex's page" });
      const doc = main.properties.doc;
      assertEq(JSON.stringify([doc?.format, doc?.ext]), JSON.stringify(["text", ".tex"]), "a LaTeX file");
      hash1 = doc.hash;
      assert(hash1, "its bytes are hashed");
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        assertEq((await page.locator(".docTextView .docTextLang").innerText()).trim(), "LaTeX", "the bar names the language");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let made = null;
    await step("text anchors: a selection's popup makes a highlight of its line and columns, with the text around it and the file's version; its row's dot names the line and jumps to it", async () => {
      const needle = "Weight decay is the oldest remedy";
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        await dragSelectCodeText(page, needle);
        const tip = page.locator(".plainTip");
        await tip.waitFor();
        await tip.locator(".colorBtn").first().click();
        made = await until(async () => (await user.api(`/api/blocks/${main.id}/subtree`)).block.children
          .find((b) => b.properties?.text_position && b.properties.quote === needle) || null, { what: "the highlight's block" });
        const pos = made.properties.text_position;
        const col = LINES[15].indexOf(needle);
        const span = { line: 16, col, end: 16, end_col: col + needle.length };
        assertEq(JSON.stringify([pos.line, pos.col, pos.end, pos.end_col]), JSON.stringify([span.line, span.col, span.end, span.end_col]), "its line and columns");
        assertEq(pos.hash, hash1, "measured on the file as it is");
        const around = contextOf(LINES, span);
        assertEq(pos.prefix, around.prefix, "the text before it");
        assertEq(pos.suffix, around.suffix, "the text after it");
        await range(page, made.id).waitFor();
        assertEq(await page.evaluate(() => window.getSelection().toString()), "", "the popup let go of the selection, which the view does not draw again");
        assertNoProblems(page);

        await page.reload();
        await codeShown(page);
        await showNotes(page);
        const dot = page.locator(`.blockRowWrap[data-block-id="${made.id}"] .highlightDotBtn`).first();
        await dot.waitFor({ timeout: 10000 });
        assertEq(await dot.getAttribute("title"), "Jump to line 16");
        await dot.click();
        await page.locator(`${CODE} [data-hl-id="${made.id}"].docHlFlash`).first().waitFor({ timeout: 3000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let first = null;
    await step("text anchors: Comment on a selection opens its card in the column beside the text, level with its line, and the thread reaches open.json and open.md by its line", async () => {
      const needle = "This note measures how much it helps";
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        await dragSelectCodeText(page, needle);
        await page.locator(".plainTip").getByRole("button", { name: "Comment on this passage" }).click();
        const fresh = column(page).locator(".commentCard.active .commentNew");
        await fresh.waitFor({ timeout: 10000 });
        await focused(field(fresh), "the new comment's field takes the focus");
        await page.keyboard.type("Say how much, in numbers.");
        await page.keyboard.press("Shift+Enter");
        const thread = await until(async () => (await threadsOf(main.id)).find((th) => th.comments[0]?.body === "Say how much, in numbers."),
          { what: "the thread is stored" });
        first = { block: thread.block_id, thread: thread.id };
        const block = await blockOf(main.id, first.block);
        assertEq(block.properties.quote, needle, "its block is the selection's range");
        assertEq(block.properties.text_position.line, 17);
        await column(page).locator(`.commentCard[data-card-for="${first.block}"]`).waitFor();
        await until(async () => {
          const look = await page.evaluate(([code, id]) => ({
            card: document.querySelector(`.docTextView .commentCard[data-card-for="${id}"]`).getBoundingClientRect().top,
            line: document.querySelector(`${code} [data-hl-id="${id}"]`).closest(".cm-line").getBoundingClientRect().top,
          }), [CODE, first.block]);
          return Math.abs(look.card - look.line) <= 30;
        }, { what: "the card level with line 17" });
        await page.keyboard.press("Escape");

        const doc = await openJsonWhen((d) => d.entries?.some((e) => e.id === first.thread), "open.json with the thread");
        const entry = doc.entries.find((e) => e.id === first.thread);
        assertEq(JSON.stringify([entry.file, entry.format, entry.kind]), JSON.stringify(["main.tex", "text", "lines"]), "a range of main.tex's lines");
        assertEq(JSON.stringify([entry.where.line, entry.where.quote, entry.where.placed]), JSON.stringify([17, needle, "exact"]), "where it is");
        assert(!("end" in entry.where), "one line, no end");
        assertEq(JSON.stringify([entry.source?.file, entry.source?.line, entry.source?.version]), JSON.stringify(["main.tex", 17, "current"]),
          "the file's own line is its source");
        assertEq(entry.picture, null, "no picture of a text file");
        assert(openMd().includes(`Line 17 · “${needle}”`), "open.md heads it by its line");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    const ten = {};
    await step("text anchors: ten threads on whole lines, then main.tex rewritten under them; the vault's round places them again", async () => {
      for (const n of TEN) {
        const span = { line: n, col: 0, end: n, end_col: [...LINES[n - 1]].length };
        const { quote, prefix, suffix } = contextOf(LINES, span);
        const block = await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: "",
          properties: { quote, color: YELLOW, text_position: { ...span, prefix, suffix, hash: hash1 } } } });
        const { thread } = await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: block.id, body: `About line ${n}.` } });
        ten[n] = { block: block.id, thread: thread.id, quote };
      }
      write(path.join(paperDir, "main.tex"), rewritten(LINES));
      await sync();
      await until(async () => (await blockOf(main.id, ten[14].block)).properties.text_position.line === 17,
        { timeout: 30000, what: "line 14's range placed at line 17" });
    });

    let hash2 = "";
    await step("text anchors: the survivors moved to their new lines, the reworded two dashed with the text they were made on, the two gone lost where they were", async () => {
      hash2 = await docHash(main.id);
      assert(hash2 && hash2 !== hash1, "the page shows the new version");
      for (const n of TEN) {
        const b = await blockOf(main.id, ten[n].block);
        const pos = b.properties.text_position;
        if (LOST.includes(n)) {
          assert(pos.lost === true && !pos.approx, `line ${n} lost: ${JSON.stringify(pos)}`);
          assertEq(JSON.stringify([pos.line, pos.hash]), JSON.stringify([n, hash1]), `line ${n} kept where it was, on the version it was made on`);
          assertEq(b.properties.quote, ten[n].quote, `line ${n} keeps its quote`);
        } else if (ROUGH.includes(n)) {
          assert(pos.approx === true && !pos.lost && pos.match >= 0.8 && pos.match <= 0.99, `line ${n} placed roughly: ${JSON.stringify(pos)}`);
          assertEq(JSON.stringify([pos.line, pos.hash]), JSON.stringify([NOW[n], hash2]), `line ${n} at its new line`);
          assertEq(JSON.stringify([pos.prev?.line, pos.prev?.hash, pos.prev?.quote]), JSON.stringify([n, hash1, ten[n].quote]), `line ${n} keeps where it was`);
          assert(b.properties.quote !== ten[n].quote, `line ${n}'s quote is spelled as the file has it now`);
        } else {
          assert(!("lost" in pos) && !("approx" in pos) && !("match" in pos), `line ${n} solid: ${JSON.stringify(pos)}`);
          assertEq(JSON.stringify([pos.line, pos.hash]), JSON.stringify([NOW[n], hash2]), `line ${n} moved to line ${NOW[n]}`);
        }
      }
      assert((await blockOf(main.id, ten[23].block)).properties.quote.includes("without any weight decay"), "the caption as reworded");
      assert((await blockOf(main.id, ten[36].block)).properties.quote.includes("one ten-thousandth"), "the range as reworded");
      assertEq((await blockOf(main.id, made.id)).properties.text_position.line, 19, "the highlight made in the view moved too");
      assertEq((await blockOf(main.id, first.block)).properties.text_position.line, 20, "and the first thread's range");
      // nothing of the deleted paragraph is near enough; the replaced sentence's start is
      assert(!(await blockOf(main.id, ten[47].block)).properties.text_position.closest, "line 47 has no closest match");
      assertEq((await blockOf(main.id, ten[55].block)).properties.text_position.closest?.line, 54, "line 55's closest match is the new sentence");
    });

    await step("text anchors: the code view draws the moved and dashed ranges over their text and the lost ones as tags at their old lines, and the column holds every card", async () => {
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        const all = [...Object.values(ten).map((t) => t.block), made.id, first.block].sort();
        await until(async () => JSON.stringify(await drawn(page)) === JSON.stringify(all), { timeout: 15000, what: "the twelve ranges drawn" });
        assertEq(JSON.stringify(await drawn(page, ".docHlApprox")), JSON.stringify(ROUGH.map((n) => ten[n].block).sort()), "the two placed roughly are dashed");
        assertEq(JSON.stringify(await drawn(page, ".docHlLost")), JSON.stringify(LOST.map((n) => ten[n].block).sort()), "the two lost are tags");
        const tagLines = await page.evaluate(([code, ids]) => ids.map((id) => document.querySelector(`${code} .docHlLost[data-hl-id="${id}"]`)
          .closest(".cm-line").textContent), [CODE, LOST.map((n) => ten[n].block)]);
        assertEq(JSON.stringify(tagLines), JSON.stringify(["study.", "\\end{document}"]), "each tag at the start of its old line, over nothing of its text");
        assertEq(await page.locator(`${CODE} > .cm-line.docHlLine.lost`).count(), 2, "the line cue dashed on the two lost lines");
        assert(await page.locator(`${CODE} > .cm-line.docHlLine`).count() >= 10, "and a cue on every line a range covers");

        await until(async () => (await column(page).locator(".commentCard").count()) === 11, { timeout: 10000, what: "eleven cards beside the text" });
        await until(async () => (await column(page).locator('.commentCard [data-place="lost"]').count()) === 2, { what: "two cards say not found" });
        assertEq(await column(page).locator('.commentCard [data-place="moved"]').count(), 2, "two cards say moved");
        assert((await column(page).locator('.commentCard [data-place="lost"]').first().innerText()).includes("The passage was not found in the new version"),
          "in those words");

        await page.locator(`${CODE} .docHlLost[data-hl-id="${ten[47].block}"]`).click({ button: "right" });
        await menuItem(page, "Move to a selection…").waitFor();
        assertEq(await menuItem(page, "Use the closest match").count(), 0, "a deleted paragraph has no closest match");
        await closeMenu(page);
        await page.locator(`${CODE} .docHlLost[data-hl-id="${ten[55].block}"]`).click({ button: "right" });
        await menuItem(page, /^Use the closest match \(\d+%\)$/).waitFor();
        await closeMenu(page);
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text anchors: the Comments window lists the eleven threads, and open.json and open.md say where each is now", async () => {
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        const panel = await openComments(page);
        await until(async () => (await panel.locator(".commentsPanelRow").count()) === 11, { timeout: 10000, what: "eleven open rows" });
        assert((await panel.locator(`.commentsPanelRow[data-thread-id="${ten[47].thread}"] .commentsPanelAnchor`).innerText())
          .includes("validation loss was lowest"), "a lost thread's row keeps its quote");
        assertNoProblems(page);
      } finally { await ctx.close(); }

      const doc = await openJsonWhen((d) => d.entries?.length === 11
        && LOST.every((n) => d.entries.find((e) => e.id === ten[n].thread)?.where.placed === "lost"), "open.json after the rewrite");
      const entryOf = (id) => doc.entries.find((e) => e.id === id);
      assert(doc.entries.every((e) => e.file === "main.tex" && e.kind === "lines" && e.source?.file === "main.tex"), "every entry by main.tex's own lines");
      for (const n of TEN) {
        const { where, source } = entryOf(ten[n].thread);
        if (LOST.includes(n)) {
          assertEq(JSON.stringify([where.placed, where.line, source.line, source.version]), JSON.stringify(["lost", n, n, "commented"]),
            `line ${n}: lost, by the lines it was commented at`);
        } else if (ROUGH.includes(n)) {
          assertEq(JSON.stringify([where.placed, where.line, source.line, source.version]), JSON.stringify(["rough", NOW[n], NOW[n], "current"]),
            `line ${n}: placed roughly at its new line`);
          assert(typeof where.match === "number" && where.match >= 0.8, `line ${n}'s match: ${where.match}`);
          assertEq(where.was, ten[n].quote, `line ${n}: the text the comment was made on`);
        } else {
          assertEq(JSON.stringify([where.placed, where.line, source.line, source.version]), JSON.stringify(["exact", NOW[n], NOW[n], "current"]),
            `line ${n}: placed exactly at line ${NOW[n]}`);
        }
      }
      assertEq(entryOf(first.thread).where.line, 20, "the first thread at its new line");
      const md = openMd();
      assert(md.includes("- was: “"), "open.md says what a rough one was");
      assert(md.includes("not found in the current version"), "and that a lost one is not found");
    });

    await step("text anchors: a lost range takes its closest match from its menu, dashed at the new sentence, and Accept makes it solid", async () => {
      const id = ten[55].block;
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(main.id));
      try {
        await codeShown(page);
        await page.locator(`${CODE} .docHlLost[data-hl-id="${id}"]`).click({ button: "right" });
        await menuItem(page, /^Use the closest match \(\d+%\)$/).click();
        const placed = await until(async () => {
          const b = await blockOf(main.id, id);
          return b.properties.text_position.approx === true ? b : null;
        }, { what: "the range placed at its closest match" });
        const pos = placed.properties.text_position;
        assertEq(JSON.stringify([pos.line, pos.hash, pos.lost]), JSON.stringify([54, hash2, undefined]), "at line 54, on the file as it is");
        assertEq(JSON.stringify([pos.prev?.line, pos.prev?.hash]), JSON.stringify([55, hash1]), "where it was kept as its reference");
        assert(placed.properties.quote.startsWith("is to repeat the study"), `the new sentence's words: ${placed.properties.quote}`);
        await page.locator(`${CODE} [data-hl-id="${id}"].docHlApprox`).first().waitFor({ timeout: 5000 });

        await range(page, id).click({ button: "right" });
        await menuItem(page, /^Accept$/).click();
        await until(async () => {
          const p = (await blockOf(main.id, id)).properties.text_position;
          return !("approx" in p) && !("prev" in p) && p.line === 54;
        }, { what: "the range accepted where it is" });
        await until(async () => !(await page.locator(`${CODE} [data-hl-id="${id}"].docHlApprox`).count()), { what: "drawn solid" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text anchors: a selection is the chat's chip by its line and file, a Ctrl+selection adds the next, and the message carries both by their lines", async () => {
      const one = "Figure~\\ref{fig:loss} shows the run", two = "fifth epoch the test loss";
      const ctx = await desktop();
      const requests = [];
      await ctx.route("**/api/ai/chat", async (route) => {
        requests.push(route.request().postDataJSON());
        await route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ delta: "Seen." })}\n` });
      });
      const page = await openPage(ctx, at(main.id));
      try {
        const input = page.getByRole("combobox", { name: "Message AI" });
        await input.waitFor({ timeout: 15000 });
        await codeShown(page);
        const chips = page.locator(".chatSelChips .chatSelChip");
        const looks = () => chips.evaluateAll((els) => els.map((el) => [el.querySelector(".chatSelChipLabel")?.textContent.trim(),
          el.querySelector(".chatSelChipNote")?.textContent.trim()]));
        await dragSelectCodeText(page, one, { moveOn: false });
        await until(async () => (await chips.count()) === 1, { what: "the selection's chip" });
        assertEq(JSON.stringify(await looks()), JSON.stringify([["Line 33", "· main.tex"]]), "named by its line and file");
        // let go of the selection, so no popup opens over the next line as the
        // hand moves to it: collapsed where it ends, which the view reads as
        // its own (a selection removed outright would still be the view's,
        // and the next press a drag of it)
        await page.evaluate(() => window.getSelection().collapseToEnd());
        await dragSelectCodeText(page, two, { moveOn: false, additive: true });
        await until(async () => (await chips.count()) === 2, { what: "the second chip" });
        assertEq(JSON.stringify(await looks()), JSON.stringify([["Line 33", "· main.tex"], ["Line 34", "· main.tex"]]), "both by their lines");
        await input.fill("What do these say?");
        await input.press("Enter");
        await until(() => requests.length === 1, { what: "the chat request" });
        assertEq(JSON.stringify(requests[0].selections.map((s) => [s.text, s.page, s.line, s.end, s.file])),
          JSON.stringify([[one, 0, 33, 33, "main.tex"], [two, 0, 34, 34, "main.tex"]]), "the passages go by their lines");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("text anchors: on a phone a finger's selection gets the popup, Comment opens the popover, and the range's badge opens its thread again", async () => {
      const needle = "the loss on held-out data";
      const ctx = await user.context(browser, { userAgent: DESKTOP_UA, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
      const page = await openPage(ctx, at(main.id));
      try {
        await page.locator(".docTextView:visible").waitFor({ timeout: 20000 });
        await codeShown(page);
        // a finger's press first, so the selection is read as a finger's (no
        // mouseup: it settles); a long press is not emulated, so the
        // selection is set by script
        await page.locator(`${CODE} > .cm-line`, { hasText: "\\usepackage{graphicx}" }).tap();
        assert(await page.evaluate(codeNeedle, [CODE, needle, true]), `"${needle}" in the code view`);
        const tip = page.locator(".plainTip");
        await tip.waitFor({ timeout: 2000 });
        await tip.getByRole("button", { name: "Comment on this passage" }).tap();
        const popover = page.locator(".commentPopover");
        const fresh = popover.locator(".commentNew");
        await fresh.waitFor({ timeout: 10000 });
        assertEq(await page.locator(".docTextView .commentMargin").count(), 0, "no column on a phone");
        await field(fresh).tap();
        await page.keyboard.type("Seen on a phone");
        await fresh.locator(".commentSend").first().tap();
        const thread = await until(async () => (await threadsOf(main.id)).find((th) => th.comments[0]?.body === "Seen on a phone"),
          { what: "the thread is stored" });
        assertEq((await blockOf(main.id, thread.block_id)).properties.text_position.line, 18, "on the selection's line");
        await popover.locator(".commentPopHead .uiClose").tap();
        await popover.waitFor({ state: "detached" });
        const badge = page.locator(`.docTextView .docTextOverlay .pdfNoteBadge.hasThread[data-hl-id="${thread.block_id}"]`);
        await badge.waitFor({ timeout: 10000 });
        await badge.tap();
        await popover.locator(".commentBody", { hasText: "Seen on a phone" }).waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no sideways scroll");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

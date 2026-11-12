import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { marginLeft } from "../../../src/comments/marginLayout.js";
import { Account, ROOT, Server, wanted } from "../harness.mjs";
import { dragSelectPdfText, waitForPdf } from "./pdf.mjs";

// Feedback on a work folder's files (docs/dev/feedback.md): the comments the
// person makes on a paper's PDF in a work folder, read by an agent through
// the files Gamma writes into the directory (.gamma/feedback/) and the
// feedback API, and answered as the agent's own comments. The vaults recipe
// (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent) over the
// SyncTeX sample paper (backend/tests/samples/synctex/paper, built twice):
// the work folder, a comment on a passage and on an area, a thread on
// handwriting, Send from the Comments window's Feedback dialog, a script
// holding a feedback token that answers (a rebuild between, so the passage
// is placed again and its source line moves), what the person sees of it
// (the Inbox, the margin, the dialog, the files), a reopen, and the files
// gone with the vault.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const PAGE = { width: 612, height: 792 };

// A position on page 1 of the paper from places.json's fraction rects, in
// the page's points as the viewer stores them.
function positionOf(rects, area = false) {
  const px = rects.map(([x1, y1, x2, y2]) => ({ x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height }));
  const bound = { x1: Math.min(...px.map((r) => r.x1)), y1: Math.min(...px.map((r) => r.y1)),
    x2: Math.max(...px.map((r) => r.x2)), y2: Math.max(...px.map((r) => r.y2)) };
  const pos = { pageNumber: 1, ...PAGE, boundingRect: bound, rects: px };
  if (area) pos.area = true;
  return pos;
}

export async function feedbackScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep } = env;
  if (!wanted("feedback")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-feedback-"));
  try {
    await server.start();
    await server.manage("create-user", "fb-user", "fb-pw");
    const user = await new Account(server, "fb-user", "fb-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const desktop = () => user.context(browser, { userAgent: DESKTOP_UA });
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    const copyPaper = (version, into) => {
      for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
        write(path.join(into, rel), fs.readFileSync(path.join(PAPER, version, rel)));
      }
    };
    const paperDir = path.join(disk, "paper");
    copyPaper("v1", paperDir);
    const feedbackDir = path.join(paperDir, ".gamma", "feedback");
    const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
    const openJson = () => readJson(path.join(feedbackDir, "open.json"));
    const filesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const threadsOf = async (page, state = "all") => (await user.api(`/api/pages/${page}/comments?state=${state}`)).threads;
    const blockOf = async (page, id) => {
      const walk = (b) => { if (b.id === id) return b; for (const c of b.children || []) { const f = walk(c); if (f) return f; } return null; };
      return walk((await user.api(`/api/blocks/${page}/subtree`)).block);
    };
    const sync = (link) => user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
    // The PDF's margin, zoomed out until the column has room beside the page
    // (comments/marginLayout.js): the column itself stands only once a thread
    // or a new comment is there, so the room is what the zoom goes by.
    const openMargin = async (page) => {
      await page.setViewportSize({ width: 1600, height: 900 });
      const zoomOut = page.getByRole("button", { name: "Zoom out", exact: true });
      const room = () => page.evaluate(() => [document.querySelector(".pdfViewer").clientWidth,
        document.querySelector('[data-page="1"]').getBoundingClientRect().width]);
      for (let i = 0; i < 12 && marginLeft(...(await room())) == null; i++) {
        await zoomOut.click();
        await sleep(200);
      }
      assert(marginLeft(...(await room())) != null, "the page has room for the column beside it");
      return page.locator('[data-page="1"] .commentMargin');
    };
    const field = (scope) => scope.locator(".commentField .cm-content");
    const focused = (f, what) => until(async () => f.evaluate((el) => el === document.activeElement).catch(() => false), { what });
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
    const dialog = (page) => page.getByRole("dialog", { name: "Feedback", exact: true });

    let link = null, main = null, W = "";
    await step("feedback: a work folder of the paper tracks its PDF, whose page is no library page", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      W = link.folder_id;
      await sync(link);
      main = await until(async () => (await filesOf(W)).find((p) => p.properties?.file_name === "main.pdf"), { timeout: 30000, what: "main.pdf's page" });
      assert(!(await user.api("/api/blocks/root/children")).children.some((p) => p.id === main.id), "the listing leaves the file out");
    });

    let area = null, areaThread = null, passageThread = null, passageBlock = "", passageLine = 0;
    await step("feedback: a comment on an area and on a passage reach open.json within seconds, with their source lines and pictures", async () => {
      const v1 = PLACES.v1;
      area = await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: "",
        properties: { pdf_position: positionOf([v1.figure.rect], true), color: "rgba(255, 226, 143, 0.65)" } } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const margin = await openMargin(page);
        // the area's thread, from its menu: a card of its own in the margin
        await page.locator(`[data-page="1"] [data-hl-id="${area.id}"]`).first().click({ button: "right" });
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Comment" }).first().click();
        const fresh = margin.locator(".commentCard.active .commentNew");
        await fresh.waitFor();
        await focused(field(fresh), "the new comment's field takes the focus");
        await page.keyboard.type("move the legend inside the plot");
        await page.keyboard.press("Shift+Enter");
        areaThread = await until(async () => (await threadsOf(main.id)).find((th) => th.block_id === area.id), { what: "the area's thread is stored" });
        await page.keyboard.press("Escape");
        // the passage's, from the selection popup's Comment
        const tip = page.locator(".plainTip");
        await dragSelectPdfText(page, 1, "penalty keeps");
        await tip.waitFor();
        await tip.getByRole("button", { name: "Comment on this passage" }).click();
        await fresh.waitFor();
        await focused(field(fresh), "the passage's field takes the focus");
        await page.keyboard.type("lambda is never introduced.");
        await page.keyboard.press("Shift+Enter");
        passageThread = await until(async () => (await threadsOf(main.id)).find((th) => th.block_id !== area.id && th.block_id !== main.id),
          { what: "the passage's thread is stored" });
        passageBlock = passageThread.block_id;
        await page.keyboard.press("Escape");

        // the files: written by the hot pass, no sync asked for
        const doc = await until(() => {
          try {
            const d = openJson();
            return d.entries?.length === 2 ? d : null;
          } catch { return null; }
        }, { timeout: 8000, what: "open.json with the two entries" });
        assertEq(doc.format, "gamma-feedback-1");
        assert(doc.entries.every((e) => e.file === "main.pdf"), "both under main.pdf");
        const byId = Object.fromEntries(doc.entries.map((e) => [e.id, e]));
        const passage = byId[passageThread.id], figure = byId[areaThread.id];
        assertEq(passage.kind, "passage");
        assertEq(passage.source?.file, "sections/method.tex", "the passage's source");
        assert(passage.source.line >= 2 && passage.source.line <= 5, `a line of the passage: ${passage.source.line}`);
        passageLine = passage.source.line;
        assertEq(figure.kind, "area");
        assertEq(JSON.stringify([figure.source?.file, figure.source?.line]), JSON.stringify(["main.tex", v1.figure.line]), "the figure's \\includegraphics line");
        await until(() => doc.entries.every((e) => e.picture && fs.existsSync(path.join(feedbackDir, e.picture))),
          { timeout: 8000, what: "each entry's picture written" });
        assert(fs.readFileSync(path.join(feedbackDir, "open.md"), "utf8").includes("## main.pdf"), "open.md names the file");
        assertEq(fs.readFileSync(path.join(feedbackDir, ".gitignore"), "utf8"), "*\n", "the files' own .gitignore");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let inkThread = null, inkGroup = null;
    await step("feedback: a thread on handwriting has a card level with the stroke, a press on the stroke keeps it, and the next stroke starts a new group", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const margin = await openMargin(page);
        await page.click("button[aria-label='Handwriting tools']");
        await page.waitForSelector(".pdfInkBar");
        const box = await page.locator('[data-page="1"]').boundingBox();
        const groups = async () => (await user.api(`/api/blocks/${main.id}/subtree`)).block.children.filter((b) => b.properties?.ink_url);
        // a stroke over the figure, with the pen armed
        const figure = PLACES.v1.figure.rect;
        const from = [box.x + box.width * (figure[0] + 0.05), box.y + box.height * (figure[1] + 0.05)];
        const to = [box.x + box.width * (figure[2] - 0.05), box.y + box.height * (figure[3] - 0.05)];
        await page.mouse.move(from[0], from[1]);
        await page.mouse.down();
        await page.mouse.move(to[0], to[1], { steps: 12 });
        await page.mouse.up();
        inkGroup = await until(async () => (await groups()).find((b) => b.properties.ink_strokes === 1) || null, { what: "the stroke's group saved" });
        // the lasso round it, then Comment
        await page.keyboard.press("l");
        const pad = 20;
        await page.mouse.move(Math.min(from[0], to[0]) - pad, Math.min(from[1], to[1]) - pad);
        await page.mouse.down();
        for (const [x, y] of [[Math.max(from[0], to[0]) + pad, Math.min(from[1], to[1]) - pad], [Math.max(from[0], to[0]) + pad, Math.max(from[1], to[1]) + pad],
          [Math.min(from[0], to[0]) - pad, Math.max(from[1], to[1]) + pad], [Math.min(from[0], to[0]) - pad, Math.min(from[1], to[1]) - pad]]) {
          await page.mouse.move(x, y, { steps: 4 });
        }
        await page.mouse.up();
        await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
        await page.locator(".inkEditMenu .inkEditRow").getByRole("button", { name: "Comment", exact: true }).click();
        const fresh = margin.locator(".commentCard.active .commentNew");
        await fresh.waitFor();
        await focused(field(fresh), "the new comment's field takes the focus");
        await page.keyboard.type("arrow: here");
        await page.keyboard.press("Shift+Enter");
        inkThread = await until(async () => (await threadsOf(main.id)).find((th) => th.block_id === inkGroup.id), { what: "the ink thread is stored" });
        const card = margin.locator(`.commentCard[data-card-for="${inkGroup.id}"]`);
        await card.waitFor();
        // level with the stroke
        const look = await page.evaluate((id) => {
          const card = document.querySelector(`.commentCard[data-card-for="${id}"]`).getBoundingClientRect();
          const stroke = document.querySelector(`[data-page="1"] g[data-ink-id="${id}"]`).getBoundingClientRect();
          return { card: card.top, stroke: stroke.top };
        }, inkGroup.id);
        assert(Math.abs(look.card - look.stroke) <= 40, `the card is level with the stroke: card ${look.card}, stroke ${look.stroke}`);
        // a press on the stroke keeps the active card; one elsewhere lets it go
        const active = margin.locator(".commentCard.active");
        await active.waitFor();
        const strokeEl = page.locator(`[data-page="1"] g[data-ink-id="${inkGroup.id}"]`);
        await strokeEl.dispatchEvent("pointerdown", { bubbles: true });
        await sleep(200);
        assertEq(await active.count(), 1, "the press on the stroke keeps the card");
        await strokeEl.dispatchEvent("pointercancel", { bubbles: true }); // (the press the ink layer saw ends, drawing nothing)
        // open.json lists it as handwriting
        await until(() => {
          try { return openJson().entries.some((e) => e.id === inkThread.id && e.kind === "ink"); } catch { return false; }
        }, { timeout: 8000, what: "the ink entry in open.json" });
        // the next stroke starts a new group: the commented one keeps its one stroke
        await page.keyboard.press("Escape");
        await active.waitFor({ state: "detached" });
        await page.keyboard.press("p");
        const paths = page.locator('[data-page="1"] .inkLayer path');
        const drawn = await paths.count();
        const after = await page.locator('[data-page="1"]').boundingBox(); // (the page moved beside its margin)
        await page.mouse.move(after.x + 100, after.y + 700);
        await page.mouse.down();
        await page.mouse.move(after.x + 250, after.y + 720, { steps: 12 });
        await page.mouse.up();
        await until(async () => (await paths.count()) === drawn + 1, { what: "the second stroke drawn" });
        await until(async () => (await groups()).length === 2, { what: "a second group" });
        assertEq((await groups()).find((b) => b.id === inkGroup.id).properties.ink_strokes, 1, "the commented group took no stroke");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let request = null;
    await step("feedback: Send from the Comments window's Feedback lists the three comments and makes a request, whose file and chips follow", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const margin = await openMargin(page);
        await margin.locator(".commentCard").nth(2).waitFor();
        await openComments(page);
        await page.locator(".dockWindow:has(.commentsPanel)").getByRole("button", { name: "Feedback", exact: true }).click();
        const d = dialog(page);
        await d.locator(".feedbackEntry").nth(2).waitFor({ timeout: 10000 });
        assertEq(await d.locator(".feedbackEntry").count(), 3, "the three comments");
        assertEq((await d.locator(".feedbackFileName").first().innerText()).trim(), "main.pdf", "under their file");
        await until(() => d.locator(".feedbackEntryPicture").evaluateAll((imgs) => imgs.length === 3 && imgs.every((i) => i.complete && i.naturalWidth > 0)),
          { timeout: 20000, what: "the thumbnails loaded" });
        await d.getByRole("textbox", { name: "Instruction for the agent (optional)" }).fill("fix these");
        await d.getByRole("button", { name: "Send 3 comments", exact: true }).click();
        await d.locator(".feedbackRequest").waitFor({ timeout: 10000 });
        assert((await d.locator(".feedbackRequest").innerText()).includes("Waiting for the agent"), "the request heads the list");
        const listed = await user.api("/api/feedback/requests?after=0");
        assertEq(listed.requests.length, 1, "one request");
        request = listed.requests[0];
        assertEq(request.state, "sent");
        assertEq(request.note, "fix these");
        assertEq(request.threads.length, 3, "its three threads");
        await until(() => fs.existsSync(path.join(feedbackDir, "requests", `${request.id}.json`)), { timeout: 8000, what: "the request file" });
        assertEq(readJson(path.join(feedbackDir, "requests", `${request.id}.json`)).entries.length, 3, "with three entries");
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        await until(async () => (await margin.locator('.commentCard [data-sent="sent"]').count()) === 3, { timeout: 10000, what: "the cards show Sent" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let token = null;
    await step("feedback: a script with a feedback token takes the request, rebuilds the paper, replies, resolves and is refused everything else", async () => {
      token = await user.api("/api/integrations/tokens", { method: "POST", body: { name: "Claude Code", scope: "feedback" } });
      const agent = async (pathname, { method = "GET", body } = {}) => {
        const r = await fetch(`${server.base}${pathname}`, { method, headers: { Authorization: `Bearer ${token.token}`,
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
        return { status: r.status, json: r.status === 204 ? null : await r.json().catch(() => null) };
      };
      const polled = await agent("/api/feedback/requests?after=0");
      assertEq(polled.status, 200);
      assertEq(polled.json.requests[0].id, request.id, "the script sees the request");
      assertEq((await agent(`/api/feedback/requests/${request.id}/state`, { method: "POST", body: { state: "running" } })).status, 200);
      // the rebuild: v2 over the build, taken by a round
      const before = JSON.stringify((await blockOf(main.id, passageBlock)).properties.pdf_position.boundingRect);
      copyPaper("v2", paperDir);
      await sync(link);
      await until(async () => JSON.stringify((await blockOf(main.id, passageBlock)).properties.pdf_position.boundingRect) !== before,
        { timeout: 30000, what: "the passage's highlight placed again" });
      const doc = (await agent(`/api/feedback?scope=${W}`)).json;
      const passage = doc.entries.find((e) => e.id === passageThread.id);
      assertEq(passage.source?.file, "sections/method.tex");
      assertEq(passage.source.line, passageLine + 3, "the source line moved by the three lines inserted");
      // the answers
      for (const th of [passageThread, areaThread, inkThread]) {
        const reply = await agent(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: th.block_id, thread_id: th.id, body: `Done for ${th.id.slice(0, 4)}` } });
        assertEq(reply.status, 201, `the reply to ${th.id}: ${JSON.stringify(reply.json)}`);
      }
      for (const th of [passageThread, areaThread]) {
        assertEq((await agent(`/api/comments/${th.id}/resolve`, { method: "POST", body: { resolved: true } })).status, 200);
      }
      assertEq((await agent(`/api/feedback/requests/${request.id}/state`, { method: "POST", body: { state: "done" } })).status, 200);
      // refused: a new thread, a block edit, a comment edit, a reopen
      assertEq((await agent(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: main.id, body: "a new thread" } })).status, 403);
      assertEq((await agent(`/api/blocks/${passageBlock}`, { method: "PUT", body: { content: "renamed" } })).status, 403);
      const replyId = (await threadsOf(main.id)).find((th) => th.id === inkThread.id).comments[1].id;
      assertEq((await agent(`/api/comments/${replyId}`, { method: "PATCH", body: { body: "edited" } })).status, 403);
      assertEq((await agent(`/api/comments/${passageThread.id}/resolve`, { method: "POST", body: { resolved: false } })).status, 403);
    });

    await step("feedback: the person hears of the agent's replies and resolves, the dialog says the request is done, and the files follow", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const bell = page.locator('[data-guide="header.inbox"]');
        await until(async () => (await bell.evaluate((el) => el.querySelector(".inboxBadge")?.textContent || "0")) !== "0", { what: "the bell counts" });
        await bell.click();
        const panel = page.locator(".inboxPopover");
        await panel.waitFor();
        await until(async () => !(await panel.locator(".inboxLoading").count()), { what: "the panel's listing is in" });
        await panel.locator(".inboxRow", { hasText: "Claude Code replied to a comment on" }).first().waitFor();
        await panel.locator(".inboxRow", { hasText: "Claude Code resolved your comment on" }).first().waitFor();
        const replyRow = panel.locator(".inboxRow", { hasText: "Claude Code replied to a comment on" }).first();
        await replyRow.locator(".inboxMain").click();
        // the item opens its thread, whose reply reads under the agent's name
        await page.locator(".commentThread .commentItem.reply .commentAuthor", { hasText: "Claude Code" }).first().waitFor({ timeout: 10000 });
        await page.keyboard.press("Escape");
        // the resolved threads' cards are gone from the margin
        const margin = await openMargin(page);
        await until(async () => (await margin.locator(".commentCard").count()) === 1, { timeout: 10000, what: "one card left, the handwriting's" });
        assertEq(await margin.locator(`.commentCard[data-card-for="${inkGroup.id}"]`).count(), 1);
        // the dialog lists the request as done
        await openComments(page);
        await page.locator(".dockWindow:has(.commentsPanel)").getByRole("button", { name: "Feedback", exact: true }).click();
        const d = dialog(page);
        await d.locator(".feedbackRequest").waitFor({ timeout: 10000 });
        await until(async () => (await d.locator(".feedbackRequest").innerText()).includes("Done by Claude Code"), { timeout: 10000, what: "the request's state" });
        assertEq(await d.locator(".feedbackEntry").count(), 1, "one open comment left");
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        // the files: the ink thread alone, drawn on the earlier build
        const doc = await until(() => {
          try { const dd = openJson(); return dd.entries.length === 1 ? dd : null; } catch { return null; }
        }, { timeout: 8000, what: "open.json with the ink thread alone" });
        assertEq(doc.entries[0].id, inkThread.id);
        assertEq(doc.entries[0].where.placed, "earlier", "ink is never placed again");
        assert(doc.entries[0].before, "with the picture of the region as commented on");
        await until(() => readJson(path.join(feedbackDir, "requests", `${request.id}.json`)).state === "done", { timeout: 8000, what: "the request file says done" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("feedback: a thread reopened from the Resolved tab is feedback again, with no Sent chip", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const panel = await openComments(page);
        await page.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Resolved (2)" }).click();
        const row = panel.locator(`.commentsPanelRow[data-thread-id="${passageThread.id}"]`);
        await row.waitFor();
        await row.click();
        await page.locator('.commentResolve[aria-label="Reopen"]').first().click();
        await until(async () => !(await threadsOf(main.id)).find((th) => th.id === passageThread.id).resolved_at, { what: "reopened on the server" });
        await until(() => {
          try { return openJson().entries.some((e) => e.id === passageThread.id); } catch { return false; }
        }, { timeout: 8000, what: "open.json holds it again" });
        assertEq(openJson().entries.find((e) => e.id === passageThread.id).sent, null, "its request row was answered: no Sent");
        await page.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Open (2)" }).click();
        assertEq(await panel.locator(`.commentsPanelRow[data-thread-id="${passageThread.id}"] [data-sent]`).count(), 0, "no chip on the row");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("feedback: removing the work folder takes its feedback files with it", async () => {
      await user.api(`/api/folder-links/${link.id}`, { method: "DELETE" });
      await until(() => !fs.existsSync(feedbackDir), { timeout: 15000, what: ".gamma/feedback gone" });
      assert(fs.existsSync(path.join(paperDir, "main.pdf")), "the paper's own files stay");
      await user.api(`/api/integrations/tokens/${token.id}`, { method: "DELETE" });
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

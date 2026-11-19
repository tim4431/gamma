import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { marginLeft } from "../../../src/comments/marginLayout.js";
import { Account, PYTHON, ROOT, Server, guideProgress, wanted } from "../harness.mjs";
import { dragSelectPdfText, waitForPdf } from "./pdf.mjs";
import { dragSelectCodeText } from "./textAnchors.mjs";
import { TOURS } from "../../../src/guide/tours/index.js";

// The agents on this computer (docs/dev/local_agents.md): the desktop app's
// own server, where Gamma itself starts Codex in the directory a
// conversation works in. The vaults recipe (GAMMA_FOLDER_LINKS, no shell
// secret, a desktop user agent) with local agents on and the stand-ins of
// backend/tests/fake_agents/ in place of the CLIs, so no real agent ever
// runs: the SyncTeX sample paper as a work folder with its .tex sources
// tracked, Codex turned on for the computer by
// the vault's maker; a passage of main.tex and one of a library PDF
// commented with Send to Codex ticked, each a turn of its thread answered
// there under Codex's name, the .tex edited and shown so, a question of
// Codex's own opened as a thread, the threads named in open.md on disk; a
// reply ticked Send to Codex (by its @codex) a turn of its thread, its
// chip, its answer under its name, Open in chat and the next ticked reply
// resuming the session; the chat's Codex side; Gamma's tools; the sessions
// Codex keeps of the folder, started in a terminal, opened from History;
// and the agent turned off.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAGE = { width: 612, height: 792 };
const CHAT_REPLY = "Tightened the sentence in sections/method.tex:3.";
const BUILD = "latexmk -pdf main.tex";
// A .tex is not among what a work folder tracks with no .gammaignore (notes, PDFs, pictures).
const IGNORE = "*\n!*/\n!*.tex\n!*.pdf\n";
// The code view's own text: the comment fields in its column are CodeMirror views too.
const CODE = ".docTextView .docTextHost > .cm-editor > .cm-scroller > .cm-content";
// Gamma's tools (step 2.7): the variable that alone carries a turn's token
// (gamma/local_agents/events.py TOKEN_ENV), and the message whose turn
// reads the page through them and quotes what it read.
const TOKEN_ENV = "GAMMA_AGENT_MCP_TOKEN";
const READ_ASK = "what did I highlight?";
// 3.3: this message's turn comments on a passage of its page through them.
const COMMENT_ASK = "ask the authors about noon";
const NOON = "Marigolds open at noon";
// 3.7: a comment on main.tex sent to Codex: its turn edits the file and asks
// a question of its own on line 17; one on a library PDF asks on a passage.
const TEX_ASK = "Shorten the title.";
const TEX_REPLY = "Shortened the title in main.tex:9.";
const TEX_QUESTION = "Which data set does this note measure?";
const TITLE = "Weight Decay on a Small Data Set";
const SHORT = "Weight Decay on Small Data";
const LIB_ASK = "Is this the right time of day?";
const LIB_REPLY = "Dawn is right; I asked about the second line.";
const LIB_QUESTION = "Is dusk right here?";
const SUNFLOWERS = ["Sunflowers turn east at dawn", "and west at dusk"];
// The guide's Send to hint and agent tour: two comments sent to Codex, each answered in its thread.
const TULIPS = ["Tulips close at night", "and open again by day"];
const GUIDE_ASK = "Is night the right time?";
// The chat's Steer: a turn long enough to steer a message into, answering it with its own reply.
const STEER_ASK = "tighten the method";
const STEER_REPLY = "Tightened the method.";
const STEER = "keep the equation numbers";
const GUIDE_REPLY = "Night is right.";
const GUIDE_ASK_2 = "Is day the right time to open?";
const GUIDE_REPLY_2 = "Day is right.";
// Step 5: the threads a person's Codex keeps of the paper's directory (the
// stand-in's `sessions`), one of them held by another Codex, and the
// message the opened one's next turn takes.
const TERMINAL = { id: "019b-term-fix", name: "Fix the abstract", ask: "fix the abstract's typo", reply: "Fixed it in main.tex:9." };
const HELD = { id: "019b-term-held", name: "Draft the introduction" };
const SESSION_ASK = "and the conclusion";
const SESSION_REPLY = "Concluded in main.tex.";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function localAgentScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, makePdf, sleep } = env;
  if (!wanted("local-agents")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-agents-"));
  const agentLog = path.join(disk, "agent.jsonl");
  const paperDir = path.join(disk, "paper");
  const codexHome = path.join(disk, "codex");   // where the server looks for a thread's writer lock
  const thread = (id, name, updated, turns = []) => ({ id, name, preview: name, cwd: paperDir, source: "cli",
    originator: "codex-tui", createdAt: updated - 60, updatedAt: updated, turns });
  // Both agents are the stand-ins, so neither probe reaches a CLI of this
  // computer. Codex edits the paper's method in a turn, after a pause long
  // enough to be seen working; each new session is a thread of its own id,
  // so a resumed one shows.
  const server = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    GAMMA_AGENT_CLAUDE: JSON.stringify([PYTHON, path.join(FAKES, "fake_claude.py")]),
    CODEX_HOME: codexHome,
    FAKE_AGENT: JSON.stringify({ delay: 8, log: agentLog, distinct: true, chat: CHAT_REPLY,
      // step 5: what a terminal left of the paper's directory
      sessions: { threads: [
        thread(TERMINAL.id, TERMINAL.name, 1794000300, [{ id: "t1", status: "completed", startedAt: 1794000000,
          completedAt: 1794000060, items: [
            { type: "userMessage", id: "u1", content: [{ type: "text", text: TERMINAL.ask }] },
            { type: "commandExecution", id: "c1", command: "rg abstract main.tex", cwd: paperDir, status: "completed",
              aggregatedOutput: "9: the abstract", exitCode: 0 },
            { type: "agentMessage", id: "a1", text: TERMINAL.reply, phase: "final_answer" }] }]),
        thread(HELD.id, HELD.name, 1794000100)] },
      // a turn of a folder that lets it ask (3b) asks to run the build with the network
      ask: [{ command: BUILD, reason: "The build fetches a font", host: "fonts.example" }],
      // a chat turn's reply comes as it is typed (3c): a word every half second
      typing: 0.5,
      edits: [{ path: "sections/method.tex", old: "by gradient descent.", new: "by gradient descent with weight decay $\\lambda$." }],
      turns: {
        // 2.7: this message's turn reads its page with Gamma's tools and answers with what it read
        [READ_ASK]: { gamma: [{ tool: "read_page", arguments: { page_id: "$page" } }], edits: [], delay: 0, typing: 0,
          chat: "From Gamma:\n\n{gamma}" },
        [COMMENT_ASK]: { gamma: [{ tool: "comment", arguments: { page_id: "$page", quote: NOON, pdf_page: 1, body: "Is noon right here?" } }],
          edits: [], delay: 0, typing: 0, chat: "Commented:\n\n{gamma}" },
        // 3.7: a thread's turn on main.tex edits it and asks on a line; one on a library PDF asks on a passage
        [TEX_ASK]: { edits: [{ path: "main.tex", old: TITLE, new: SHORT }],
          gamma: [{ tool: "comment", arguments: { page_id: "$page", line: 17, end_line: 17, body: TEX_QUESTION } }],
          delay: 0, typing: 0, chat: TEX_REPLY },
        [LIB_ASK]: { edits: [], gamma: [{ tool: "comment", arguments: { page_id: "$page", quote: SUNFLOWERS[1], pdf_page: 1,
          body: LIB_QUESTION } }], delay: 0, typing: 0, chat: LIB_REPLY },
        [SESSION_ASK]: { edits: [], ask: [], delay: 0, typing: 0, chat: SESSION_REPLY },
        [STEER_ASK]: { edits: [], ask: [], delay: 4, typing: 0, chat: STEER_REPLY },
        [GUIDE_ASK]: { edits: [], ask: [], delay: 0, typing: 0, chat: GUIDE_REPLY },
        [GUIDE_ASK_2]: { edits: [], ask: [], delay: 0, typing: 0, chat: GUIDE_REPLY_2 } } }),
  } });
  try {
    await server.start();
    await server.manage("create-user", "la-user", "la-pw");
    const user = await new Account(server, "la-user", "la-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const desktop = (opts = {}) => user.context(browser, { userAgent: DESKTOP_UA, ...opts });
    // What the page asks the desktop shell, recorded as window.shellAsks: no shell runs here.
    const recordShellAsks = (page) => page.evaluate(() => {
      window.shellAsks = [];
      window.addEventListener("message", (e) => { if (e.data?.source === "gamma-app") window.shellAsks.push(e.data); });
    });
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      write(path.join(paperDir, rel), fs.readFileSync(path.join(PAPER, "v1", rel)));
    }
    write(path.join(paperDir, ".gammaignore"), IGNORE);
    const annotationsDir = path.join(paperDir, ".gamma", "annotations");
    const openMd = () => fs.readFileSync(path.join(annotationsDir, "open.md"), "utf8");
    const threadsOf = async (page) => (await user.api(`/api/pages/${page}/comments?state=all`)).threads;
    const filesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const sync = () => user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
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
    // The PDF's margin, zoomed out until the column has room beside the page
    // (comments/marginLayout.js): the column itself stands only once a thread
    // or a new comment is there, so the room is what the zoom goes by.
    const openMargin = async (page) => {
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
    // A new comment's field in `scope` (a margin or the code view's column),
    // the comment typed and sent with Send to Codex ticked.
    const sendNewComment = async (page, scope, body) => {
      const fresh = scope.locator(".commentCard.active .commentNew");
      await fresh.waitFor({ timeout: 10000 });
      assertEq((await fresh.locator(".commentSendTick").innerText()).trim(), "Send to Codex", "the new comment's field offers the agent");
      const tick = fresh.locator(".commentSendTo input[type=checkbox]");
      if (!(await tick.isChecked())) await fresh.locator(".commentSendTick").click();
      await until(() => tick.isChecked(), { what: "Send to Codex ticked" });
      await fresh.locator(".commentField .cm-content").click();
      await page.keyboard.type(body);
      await page.keyboard.press("Shift+Enter");
    };
    // The thread whose first comment is `body` on the page, once Codex's
    // reply `reply` is its second comment.
    const answered = (pageId, body, reply) => until(async () => (await threadsOf(pageId))
      .find((th) => th.comments[0]?.body === body && th.comments[1]?.body === reply) || null,
    { timeout: 40000, what: `Codex's reply to "${body}"` });
    // The thread Codex opened on the page with its question `body`.
    const asked = (pageId, body) => until(async () => (await threadsOf(pageId))
      .find((th) => th.comments[0]?.body === body) || null, { timeout: 20000, what: `Codex's question "${body}"` });
    // What the stand-in heard, in order: each turn/start names the thread
    // (the agent's session) the turn ran in.
    const heard = () => fs.readFileSync(agentLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
    const turnsHeard = () => heard().filter((e) => e.in?.method === "turn/start").map((e) => e.in.params.threadId);
    // A new comment on an area of page 1, through the API (no mention).
    const commentOn = async (place, body) => {
      const area = await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: "",
        properties: { pdf_position: areaOf(place), color: "rgba(255, 226, 143, 0.65)" } } });
      const made = await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: area.id, body } });
      assert(!("turn" in made), "a comment sent to no agent starts no turn");
      return made.thread;
    };
    // A thread opened from the Comments window's row, its reply field
    // pressed open (as in Notion), in the open or the resolved list.
    const openThread = async (page, threadId, resolved = false) => {
      const panel = await openComments(page);
      if (resolved) await page.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Resolved" }).click();
      const row = panel.locator(`.commentsPanelRow[data-thread-id="${threadId}"]`);
      await row.waitFor({ timeout: 10000 });
      await row.click();
      return page.locator(".commentThread").first();
    };
    const replyField = async (thread) => {
      await thread.locator(".commentBody").first().click();
      const field = thread.locator(".commentField .cm-content").last();
      await field.waitFor();
      await field.click();
      return field;
    };
    // The chat window, opened from the View menu when it is closed.
    const openChat = async (page) => {
      const dock = page.locator(".dockWindow:has(.chatPanel)");
      if (!(await dock.count())) {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.locator(".menuPopover .ctxMenuItem", { hasText: "AI Chat" }).click();
        await page.keyboard.press("Escape");
      }
      await dock.waitFor();
      return dock;
    };
    // The agents the composer's chip lists, by name (without a row's hint, "not ready"), its menu closed again.
    const agentsListed = async (page, dock) => {
      await dock.locator(".chatModelChip .uiSelectBtn").click();
      const names = await page.locator(".uiSelectMenu [role=group]").first().getByRole("menuitemradio").locator(".ctxMenuText").allInnerTexts();
      await page.keyboard.press("Escape");
      return names.map((s) => s.trim());
    };
    // The chat's Codex side, picked in the composer's chip unless it shows
    // already (no AI service: the agent is the chat's own), its strip saying
    // the agent is there.
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
      if (!(await dock.locator(".computerStrip").count())) {
        await dock.locator(".chatModelChip .uiSelectBtn").click();
        await page.getByRole("menuitemradio", { name: "Codex", exact: true }).click();
      }
      await dock.locator(".computerStrip").waitFor();
      return dock;
    };
    // `words` selected on PDF page `n`, as a drag ending in the viewer does.
    const selectOnPdf = (page, n, words) => page.evaluate(([n, words]) => {
      const span = [...document.querySelectorAll(`[data-page="${n}"] .textLayer span`)].find((s) => s.textContent.includes(words));
      const off = span.textContent.indexOf(words);
      const range = document.createRange();
      range.setStart(span.firstChild, off);
      range.setEnd(span.firstChild, off + words.length);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      span.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    }, [n, words]);
    const conversation = async () => (await user.api(`/api/local-agents/chat?bucket=${main.id}`)).conversation;

    let link = null, main = null;
    await step("local-agents: a work folder of the paper, its .tex tracked, and Codex found on this computer", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      await sync();
      main = await until(async () => (await filesOf(link.folder_id)).find((p) => p.properties?.file_name === "main.pdf"),
        { timeout: 30000, what: "main.pdf's page" });
      const found = (await user.api("/api/local-agents?fresh=1")).agents;
      assertEq(found.map((a) => a.name).join(","), "codex,claude", "the agents Gamma knows");
      const codex = found.find((a) => a.name === "codex");
      assert(codex.found && codex.ready, `Codex is found and signed in: ${JSON.stringify(codex)}`);
      assertEq(codex.label, "Codex");
      assertEq(link.agent ?? null, null, "the folder has no agent yet");
    });

    await step("local-agents: the vault's maker turns Codex on for the computer", async () => {
      const turned = await user.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: true } });
      assertEq(JSON.stringify([turned.name, turned.label, turned.enabled, turned.runs, turned.mine]),
        JSON.stringify(["codex", "Codex", true, "never", true]), "on, the account's own");
      assertEq(turned.ready, true, "ready, from the probe the PATCH ran");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent?.name, "codex", "the listing carries the agent that works in the vault");
      assertEq(listed.agent.running, null, "nothing running");
    });

    // Step 3.5: a reply ticked by its @codex.
    let caption = null, captionTurn = "";
    await step("local-agents: @codex in a reply ticks Send to Codex, and the reply is a turn of its thread: the chip says Codex is working, then its reply comes under its name", async () => {
      caption = await commentOn(PLACES.v1.figure.rect, "the caption says 2025");
      const before = turnsHeard().length;
      const ctx = await desktop();

      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id);
        await replyField(thread);
        const tick = thread.locator(".commentSendTo input[type=checkbox]");
        assertEq(await tick.isChecked(), false, "a thread among people: as the person left the tick, off");
        assertEq((await thread.locator(".commentSendTick").innerText()).trim(), "Send to Codex", "the tick names the agent");
        await page.keyboard.type("@cod");
        const menu = page.locator(".mentionMenu");
        const row = menu.locator(".refPopupItem.selected");
        await until(async () => (await row.locator(".refPopupText").innerText().catch(() => "")).startsWith("Codex"),
          { what: "the @ menu offers the agent" });
        assertEq((await row.locator(".mentionAside").innerText()).trim(), "on this computer", "where it runs");
        assertEq(await menu.locator(".refPopupHead").allInnerTexts().then((h) => h.join("|")), "People", "under People");
        await page.keyboard.press("Enter");
        await menu.waitFor({ state: "detached" });
        await thread.locator(".cmMentionChip", { hasText: "@codex" }).waitFor();
        await until(() => tick.isChecked(), { what: "@codex ticks the box" });
        await page.keyboard.type("fix this only");
        await page.keyboard.press("Shift+Enter");
        // the chip follows the turn, with Stop, then the agent's reply lands under its name
        const chip = thread.locator(".commentTurn .stateTag");
        await until(async () => (await chip.innerText().catch(() => "")).trim().startsWith("Codex is working"),
          { timeout: 20000, what: "the chip says Codex is working" });
        await thread.locator(".commentTurnStop").waitFor();
        const answer = thread.locator(".commentItem.reply", { hasText: CHAT_REPLY });
        await answer.waitFor({ timeout: 40000 });
        assertEq((await answer.locator(".commentAuthor").textContent()).trim(), "Codex", "the reply is Codex's");
        await thread.locator(".commentTurn").waitFor({ state: "detached", timeout: 10000 });
        // the reply's own @codex is the agent's chip in the person's comment
        await thread.locator(".commentItem.reply .mentionChip.mention-agent", { hasText: "@codex" }).first().waitFor();
        // Open in chat: the thread's conversation, its steps and its reply, in the chat
        await answer.getByRole("button", { name: "Open in chat", exact: true }).click();
        const dock = page.locator(".dockWindow:has(.chatPanel)");
        await dock.locator("[data-testid=chat-thread-note]").waitFor({ timeout: 10000 });
        await dock.locator(".chatBubble.user", { hasText: "fix this only" }).waitFor();
        await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor();
        await dock.getByRole("button", { name: "Back to the chat", exact: true }).first().click();
        await dock.locator("[data-testid=chat-thread-note]").waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const stored = (await threadsOf(main.id)).find((th) => th.id === caption.id);
      const [, mine, theirs] = stored.comments;
      assertEq(mine.body, "@codex fix this only", "the reply is stored as written");
      assertEq(JSON.stringify([theirs.agent, theirs.agent_kind, theirs.author, theirs.body]),
        JSON.stringify(["Codex", "computer", "la-user", CHAT_REPLY]), "Codex answered in the thread, for the person");
      const turns = turnsHeard().slice(before);
      assertEq(turns.length, 1, "one turn");
      captionTurn = turns[0];
      assert(!turnsHeard().slice(0, before).includes(captionTurn), `a session of its own: ${captionTurn}`);
      const told = heard().filter((e) => e.in?.method === "turn/start").at(-1).in.params.input.map((p) => p.text || "").join("\n");
      assert(told.includes('"message": "fix this only"') && told.includes(`"thread_id": "${caption.id}"`)
        && told.includes("the caption says 2025"), `the comment and its thread reach Codex: ${told.slice(0, 600)}`);
    });

    await step("local-agents: in the thread Codex answered last the tick starts ticked, the field replies to Codex and its button sends to Codex, and the reply resumes the session", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id);
        await replyField(thread);
        assertEq(await thread.locator(".commentSendTo input[type=checkbox]").isChecked(), true, "the agent spoke last: ticked");
        assertEq((await thread.locator(".commentField .cm-placeholder").last().innerText()).trim(), "Reply to Codex…");
        assertEq(await thread.locator(".commentSend").last().getAttribute("aria-label"), "Send to Codex", "the button says what it starts");
        await page.keyboard.type("keep the British spelling");
        await page.keyboard.press("Shift+Enter");
        await thread.locator(".commentItem.reply", { hasText: CHAT_REPLY }).nth(1).waitFor({ timeout: 40000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const resumed = since.filter((e) => e.in?.method === "thread/resume").map((e) => e.in.params.threadId);
      assertEq(JSON.stringify(resumed), JSON.stringify([captionTurn]), "Codex's thread/resume of the thread's session");
      assertEq(since.filter((e) => e.in?.method === "thread/start").length, 0, "no new session");
      assertEq(JSON.stringify(since.filter((e) => e.in?.method === "turn/start").map((e) => e.in.params.threadId)),
        JSON.stringify([captionTurn]), "the turn ran in it");
    });

    await step("local-agents: an edit of an older comment is not sent and says why; a resolved thread is refused, the comment stored", async () => {
      const axis = await commentOn(PLACES.v1.equation.rect, "the axis label is missing");
      await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: axis.block_id, thread_id: axis.id, body: "and its units" } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, axis.id);
        const first = thread.locator(".commentItem", { hasText: "the axis label is missing" }).first();
        await first.hover();
        await first.locator(".rowMenuBtn").click();
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Edit" }).click();
        const edit = first.locator(".commentField .cm-content");
        await edit.waitFor();
        await edit.click();
        await page.keyboard.press("End");
        await page.keyboard.type(" @codex");
        await page.locator(".mentionMenu .refPopupItem.selected", { hasText: "Codex" }).waitFor();
        await page.keyboard.press("Enter");
        assertEq((await first.locator("[data-agent-hint]").innerText()).trim(), "Reply to ask Codex again: only the newest comment is sent.",
          "the field says before saving that an older comment's edit is not sent");
        await first.getByRole("button", { name: "Save", exact: true }).click();
        // the refusal stays on the thread, under its tag, with no Send again for an older comment
        const unsent = thread.locator(".commentTurn", { hasText: "Not sent to Codex" });
        await unsent.waitFor({ timeout: 10000 });
        assertEq((await unsent.locator(".stateDetail").innerText()).trim(), "Reply to ask Codex again: only the newest comment is sent.",
          "the thread says why it was not sent");
        assertEq(await unsent.getByRole("button", { name: "Send again", exact: true }).count(), 0, "an older comment's edit is not offered again");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const codex = (await user.api("/api/local-agents")).agents.find((a) => a.name === "codex");
      await user.api(`/api/comments/${caption.id}/resolve`, { method: "POST", body: { resolved: true } });
      const refused = await user.api(`/api/pages/${main.id}/comments`, { method: "POST",
        body: { block_id: caption.block_id, thread_id: caption.id, body: "once more", to: codex.id } });
      assertEq(refused.turn?.refused, "closed", "a resolved thread is reopened first");
      assertEq(refused.comment.body, "once more", "the comment is stored all the same");
    });

    // Step 3.7 of docs/research/agents-as-participants-plan.md: a comment ticked
    // Send to Codex is how a person asks the agent (docs/dev/local_agents.md
    // "A comment sent to an agent"), on a synced folder's file and on a
    // library PDF. After the steps that read the tick as the person left it:
    // ticked here, it starts ticked from then on.
    let texThread = null;
    await step("local-agents: on main.tex of the synced folder a passage commented with Send to Codex ticked is answered in its thread under Codex's name, the file edited and the code view showing it, and Codex asks a question of its own on a line", async () => {
      const tex = await until(async () => (await filesOf(link.folder_id)).find((p) => p.properties?.file_name === "main.tex"),
        { timeout: 30000, what: "main.tex's page" });
      const before = heard().length;
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(tex.id));
      const codeShown = () => until(async () => (await page.locator(`${CODE} > .cm-line`).count()) > 20,
        { timeout: 20000, what: "main.tex's lines in the code view" });
      try {
        await codeShown();
        await dragSelectCodeText(page, "Weight decay is the oldest remedy");
        await page.locator(".plainTip").getByRole("button", { name: "Comment on this passage" }).click();
        await sendNewComment(page, page.locator(".docTextView .commentMargin"), TEX_ASK);
        texThread = await answered(tex.id, TEX_ASK, TEX_REPLY);
        const reply = texThread.comments[1];
        assertEq(JSON.stringify([reply.agent, reply.agent_kind, reply.author]), JSON.stringify(["Codex", "computer", "la-user"]),
          "the reply is Codex's, for the person");
        const computer = reply.agent_on;
        assert(computer, "the reply names the computer Codex ran on");
        // the file edited on disk, and the code view shows it once the vault's round takes it. A file
        // written less than gamma_sync.SETTLE_S ago is still being written and waits for a later
        // round, so the page is reloaded only once its document carries the new bytes: before, it
        // asks for the ones the disk no longer holds (404)
        assert(fs.readFileSync(path.join(paperDir, "main.tex"), "utf8").includes(SHORT), "the edit is in the directory");
        const was = tex.properties?.doc?.hash;
        assert(was, `main.tex's page names its file: ${JSON.stringify(tex.properties?.doc)}`);
        await until(async () => {
          await sync();
          return (await user.api(`/api/blocks/${tex.id}`)).properties?.doc?.hash !== was;
        }, { timeout: 20000, what: "the vault's round took the edit" });
        await page.reload();
        await codeShown();
        await until(async () => (await page.locator(`${CODE} > .cm-line`, { hasText: SHORT }).count()) > 0,
          { timeout: 20000, what: "the code view shows the edit" });
        // Codex's own question: a thread on line 17, written for the person under its name
        const question = await asked(tex.id, TEX_QUESTION);
        assertEq(JSON.stringify([question.comments[0].agent, question.comments[0].author]), JSON.stringify(["Codex", "la-user"]),
          "the question is Codex's");
        // the thread opened: the reply under Codex's name, its byline naming the computer and the person
        await page.goto(`${at(tex.id)}&thread=${texThread.id}`);
        const item = page.locator(".commentItem.reply", { hasText: TEX_REPLY }).first();
        await item.waitFor({ timeout: 10000 });
        const byline = `Written by Codex on ${computer}, for la-user`;
        assertEq((await item.locator(".commentAuthor").textContent()).trim(), "Codex", "the agent's name");
        assertEq(await item.locator(".commentAuthor").getAttribute("title"), byline, "the byline names the computer and the person");
        assertEq(await item.locator(".commentFor").textContent(), `on ${computer}, for la-user`, "after the agent's name");
        assertEq(await item.locator(":scope > .agentAvatar").getAttribute("title"), byline, "and so does the agent's avatar");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      // the agent ran in the folder's directory, told of the thread, and got none of the server's own variables
      const since = heard().slice(before);
      const servers = since.filter((e) => e.argv?.[0] === "app-server");
      assert(servers.some((e) => path.relative(e.cwd, paperDir) === ""), `an app-server ran in the work folder: ${JSON.stringify(servers)}`);
      assert(since.every((e) => !e.env || e.env.every((name) => !name.startsWith("GAMMA_") || name === TOKEN_ENV)),
        "no GAMMA_ variable but the turn's token reached the agent");
      const turn = since.find((e) => e.in?.method === "turn/start").in.params;
      const told = turn.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(told.includes(`"message": "${TEX_ASK}"`) && told.includes(`"thread_id": "${texThread.id}"`),
        `the comment and its thread reach Codex: ${told.slice(0, 600)}`);
      assertEq(turn.sandboxPolicy?.networkAccess, false, "a sandbox without network");
      // .gamma/annotations/open.md on disk names the thread and Codex's question, for an agent at the terminal
      const question = await asked(tex.id, TEX_QUESTION);
      await until(() => {
        try { const md = openMd(); return md.includes(`\`${texThread.id}\``) && md.includes(`\`${question.id}\``); } catch { return false; }
      }, { timeout: 15000, what: "open.md names both threads" });
      assert(openMd().includes(TEX_QUESTION), "with Codex's question");
    });

    await step("local-agents: on a library PDF a passage commented with Send to Codex ticked is answered in its thread under Codex's name, and Codex asks about another passage", async () => {
      const up = await user.upload("/api/uploads", makePdf([SUNFLOWERS]), "sunflowers.pdf", "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Sunflower paper", source_url: up.source_url, original_filename: "sunflowers.pdf" } });
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(paper.id));
      try {
        await waitForPdf(page, 1);
        const margin = await openMargin(page);
        await dragSelectPdfText(page, 1, "turn east at dawn");
        await page.locator(".plainTip").getByRole("button", { name: "Comment on this passage" }).click();
        await sendNewComment(page, margin, LIB_ASK);
        const thread = await answered(paper.id, LIB_ASK, LIB_REPLY);
        assertEq(JSON.stringify([thread.comments[1].agent, thread.comments[1].agent_kind]), JSON.stringify(["Codex", "computer"]),
          "the reply is Codex's");
        const question = await asked(paper.id, LIB_QUESTION);
        assertEq(question.comments[0].agent, "Codex", "a thread of Codex's own on the passage it asks about");
        await page.goto(`${at(paper.id)}&thread=${thread.id}`);
        const item = page.locator(".commentItem.reply", { hasText: LIB_REPLY }).first();
        await item.waitFor({ timeout: 10000 });
        assertEq((await item.locator(".commentAuthor").textContent()).trim(), "Codex", "under Codex's name");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    // The guide (docs/dev/onboarding.md): writing where Codex can be asked
    // offers the Send to hint at the field's tick; a comment sent to Codex
    // offers the agent tour (a later load: one offer a load), which walks
    // its thread to the end. Every other guide is settled, so none takes
    // the load's one offer.
    await step("local-agents: writing a comment that can ask Codex offers the Send to hint, and a comment sent to it the agent tour", async () => {
      const up = await user.upload("/api/uploads", makePdf([TULIPS]), "guided.pdf", "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Guided paper", source_url: up.source_url, original_filename: "guided.pdf" } });
      const settled = Object.fromEntries(Object.values(TOURS).filter((t) => !["send-to-agent", "agent-comments"].includes(t.id))
        .map((t) => [t.id, { state: "dismissed", version: t.version }]));
      // the account's copy holds Suggest tours off from its first load, and wins over the browser's
      await user.api("/api/prefs/profile", { method: "PATCH", body: { set: { tourProgress: settled, suggestTours: true } } });
      const ctx = await desktop({ viewport: { width: 1600, height: 900 }, suggestTours: true });
      const page = await openPage(ctx, at(paper.id));
      const progress = (id) => guideProgress(page, "la-user", id);
      try {
        await waitForPdf(page, 1);
        let margin = await openMargin(page);
        await dragSelectPdfText(page, 1, "close at night");
        await page.locator(".plainTip").getByRole("button", { name: "Comment on this passage" }).click();
        const fresh = margin.locator(".commentCard.active .commentNew");
        await fresh.waitFor({ timeout: 10000 });
        await fresh.locator(".commentField .cm-content").click();
        await page.keyboard.type(GUIDE_ASK.slice(0, 2));
        await page.waitForSelector('[data-guide-offer="send-to-agent"] .guideCard .guideChip.tip');
        assertEq(await page.locator('[data-guide="comments.sendTo"][data-guide-active]').count(), 1, "the hint points at the field's tick");
        await page.getByRole("button", { name: "Got it" }).click();
        assertEq(await progress("send-to-agent"), "done");
        const tick = fresh.locator(".commentSendTo input[type=checkbox]");
        if (!(await tick.isChecked())) await fresh.locator(".commentSendTick").click();
        await fresh.locator(".commentField .cm-content").click();
        await page.keyboard.press("End");
        await page.keyboard.type(GUIDE_ASK.slice(2));
        await page.keyboard.press("Shift+Enter");
        await answered(paper.id, GUIDE_ASK, GUIDE_REPLY);
        assertEq(await page.locator("[data-guide-offer]").count(), 0, "the hint had this load's one offer");
        await page.reload();
        await waitForPdf(page, 1);
        margin = await openMargin(page);
        await dragSelectPdfText(page, 1, "open again by day");
        await page.locator(".plainTip").getByRole("button", { name: "Comment on this passage" }).click();
        await sendNewComment(page, margin, GUIDE_ASK_2);
        await page.waitForSelector('[data-guide-offer="agent-comments"] .guideCard');
        await page.getByRole("button", { name: "Show me" }).click();
        await page.waitForSelector('[data-guide-overlay="agent-thread"] .guideCard');
        // Codex's answer in the thread on screen, with its Open in chat, before the tour goes on to it
        await answered(paper.id, GUIDE_ASK_2, GUIDE_REPLY_2);
        await margin.locator(".commentCard.active .commentItem", { hasText: GUIDE_REPLY_2 }).locator('[data-guide="comments.openChat"]').waitFor();
        const primary = page.locator(".guideCard .uiBtn.primary");
        await primary.click();
        await page.waitForSelector('[data-guide-overlay="agent-chat"] .guideCard');
        assertEq(await page.locator('[data-guide="comments.openChat"][data-guide-active]').count(), 1, "Open in chat on Codex's answer");
        await primary.click();
        await page.waitForSelector('[data-guide-overlay="agent-reply"] .guideCard');
        await primary.click();
        await until(async () => await page.locator(".guideCard").count() === 0, { what: "the tour ends" });
        assertEq(await progress("agent-comments"), "done");
        assertNoProblems(page);
      } finally {
        await ctx.close();
        await user.api("/api/prefs/profile", { method: "PATCH", body: { set: { tourProgress: {}, suggestTours: false } } });
      }
    });

    // Step 3a: the chat's computer side (docs/dev/local_agents.md "The chat's computer side").
    let chatThread = "";
    await step("local-agents: the maker's chat has a Codex side, where a message with a PDF passage goes to Codex on this computer and its reply comes back", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
        assertEq((await agentsListed(page, dock)).join("|"), "Gamma AI|Codex", "the chip lists Gamma AI and the agent turned on");
        await codexSide(page);
        await until(async () => (await dock.locator(".computerStrip").innerText()).trim() === "Codex on this computer · paper",
          { what: "the strip names the agent and the folder" });
        await dock.locator(".chatEmpty", { hasText: "Ask Codex about this file" }).waitFor();
        await selectOnPdf(page, 1, "The penalty");
        await dock.locator(".chatSelChips").getByText("PDF passage", { exact: true }).waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("tighten this sentence");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.user", { hasText: "tighten this sentence" }).waitFor();
        assertEq(await dock.locator(".chatSelChips").count(), 0, "the passage went with the message");
        // working, Stop in place of Send, then the reply
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).waitFor({ timeout: 10000 });
        await until(async () => (await dock.locator(".chatSteps").last().innerText().catch(() => "")).includes("Codex is working"),
          { timeout: 15000, what: "the turn's pill says Codex is working" });
        assertEq((await dock.locator(".computerStrip").innerText()).trim(), "Codex on this computer · paper",
          "the strip keeps its line: the pill says how the turn goes");
        await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor({ timeout: 40000 });
        await dock.getByRole("button", { name: "Send", exact: true }).waitFor();
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const turns = since.filter((e) => e.in?.method === "turn/start");
      assertEq(turns.length, 1, "one turn");
      chatThread = turns[0].in.params.threadId;
      const prompt = turns[0].in.params.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(prompt.includes('"message": "tighten this sentence"') && prompt.includes("The penalty") && prompt.includes('"path": "main.pdf"'),
        `the message, the passage and the file reach Codex: ${prompt.slice(0, 600)}`);
      const stored = await conversation();
      assertEq(JSON.stringify(stored.messages.map((m) => [m.role, m.role === "agent" ? m.state : m.text])),
        JSON.stringify([["user", "tighten this sentence"], ["agent", "done"]]), "the conversation as stored");
      assertEq(stored.messages[0].selections[0].page, 1, "the passage's page");
    });

    await step("local-agents: the turn is the computer's: a tab closed mid-turn finds it again, and the follow-up resumes Codex's session", async () => {
      const before = heard().length;
      let ctx = await desktop();
      let page = await openPage(ctx, at(main.id));
      await waitForPdf(page, 1);
      let dock = await codexSide(page);
      await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor();
      await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("and the next one too");
      await dock.getByRole("button", { name: "Send", exact: true }).click();
      await dock.getByRole("button", { name: "Stop Codex", exact: true }).waitFor({ timeout: 10000 });
      await ctx.close(); // the tab goes; the turn does not
      await until(async () => (await conversation()).messages.at(-1).state === "running", { timeout: 15000, what: "the turn runs on" });
      ctx = await desktop();
      page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        dock = await codexSide(page);
        await dock.locator(".chatBubble.user", { hasText: "and the next one too" }).waitFor();
        await dock.locator(".chatSteps.live").waitFor({ timeout: 10000 });
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === 2,
          { timeout: 40000, what: "the second reply, seen in the new tab" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      assertEq(JSON.stringify(since.filter((e) => e.in?.method === "thread/resume").map((e) => e.in.params.threadId)),
        JSON.stringify([chatThread]), "the conversation's session resumed");
      assertEq((await conversation()).messages.at(-1).fresh, false, "nothing forgotten");
    });

    await step("local-agents: a message sent while Codex works waits in the queue, and its Steer hands it to Codex's turn: it shows where Codex took it in, and the reply answers it", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        const box = dock.getByRole("textbox", { name: "Message Codex", exact: true });
        await box.fill(STEER_ASK);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).waitFor({ timeout: 10000 });
        await until(async () => (await conversation()).messages.at(-1).state === "running", { timeout: 15000, what: "Codex works" });
        // A turn takes a steer once Codex has begun its own (its turn's id,
        // codex.py _steer); before that the steer is refused (409) and the
        // chat sends the message as the next one. The message is running
        // from the runner's start, before Codex is up: a loaded machine
        // widens that.
        await until(() => heard().some((e) => e.in?.method === "turn/start" && JSON.stringify(e.in.params.input).includes(STEER_ASK)),
          { timeout: 15000, what: "Codex began its turn" });
        await box.fill(STEER);
        await box.press("Enter");
        const row = dock.locator(".chatQueueRow", { hasText: STEER });
        await row.getByRole("button", { name: "Steer", exact: true }).click();
        const steered = dock.locator(".chatBubble.user", { hasText: STEER });
        await steered.waitFor({ timeout: 20000 });
        assert((await steered.innerText()).includes("Steered"), "the message says it was steered in");
        await until(async () => !(await dock.locator(".chatQueueRow").count()), { what: "it left the queue" });
        await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 40000, what: "the turn ends" });
        await dock.locator(".chatBubble.ai", { hasText: `${STEER_REPLY}; ${STEER}` }).waitFor({ timeout: 20000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const turn = (await conversation()).messages.at(-1);
      assertEq((turn.steers || []).map((s) => s.text).join(), STEER, "stored with its turn");
      assert(heard().some((e) => e.in?.method === "turn/steer" && e.in.params.clientUserMessageId === turn.steers[0].id),
        "Codex heard it under its id");
    });

    await step("local-agents: Stop ends Codex's turn, and New chat keeps the conversation in History", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("rewrite the whole method");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).click({ timeout: 10000 });
        await dock.locator(".chatBubble.ai", { hasText: "(stopped)" }).waitFor({ timeout: 20000 });
        await dock.getByRole("button", { name: "Send", exact: true }).waitFor();
        assertEq((await conversation()).messages.at(-1).state, "stopped", "stored as stopped");
        await dock.getByRole("button", { name: "New chat", exact: true }).click();
        await dock.locator(".chatEmpty", { hasText: "Ask Codex about this file" }).waitFor();
        await dock.getByRole("button", { name: "Chat history", exact: true }).click();
        const row = page.locator(".chatHistoryPop .chatHistRow", { hasText: "tighten this sentence" });
        await row.click();
        await dock.locator(".chatBubble.user", { hasText: "rewrite the whole method" }).waitFor();
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assert(await user.api("/api/local-agents/chat/history?bucket=" + main.id).then((h) => h.conversations.length === 0),
        "the opened conversation is the active one again");
    });

    // Step 3b: asking in the chat (docs/dev/local_agents.md "Asking in the chat").
    // What Codex was told of its asks, in order: the decisions, and a note's steer.
    const decisions = () => heard().filter((e) => e.answer?.result?.decision !== undefined).map((e) => e.answer.result.decision);
    const steers = () => heard().filter((e) => e.in?.method === "turn/steer").map((e) => e.in.params.input.map((p) => p.text).join(""));
    const askAndAnswer = async (message, answer) => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(message);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        const card = dock.locator(".chatAsk");
        await card.waitFor({ timeout: 20000 });
        assertEq((await card.locator(".chatApprovalHead").innerText()).trim(), "Codex wants to run a command");
        assertEq((await card.locator(".chatAskCommand").innerText()).trim(), BUILD, "the command, whole");
        assert((await card.innerText()).includes("The build fetches a font"), "its reason");
        assert((await card.innerText()).includes("It runs as you, with your files and the network."), "what allowing means");
        await until(async () => (await dock.locator(".computerStrip.asking").innerText().catch(() => "")).trim()
          === "Waiting for you: Codex wants to run a command", { what: "the strip waits for the person" });
        await answer(card);
        await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 40000, what: "the turn ends" });
        const replies = (await conversation()).messages.filter((m) => m.role === "agent" && m.text === CHAT_REPLY).length;
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === replies,
          { what: "the turn's reply in the window" });
        assertEq(await dock.locator(".chatAsk").count(), 0, "no card left");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    };

    await step("local-agents: its owner lets Codex ask, and a command it asks to run in the chat waits on a card until allowed once", async () => {
      const turned = await user.api("/api/local-agents/codex", { method: "PATCH", body: { runs: "ask" } });
      assertEq(turned.runs, "ask", "the computer lets its agent ask");
      const before = decisions().length;
      await askAndAnswer("build it", (card) => card.getByRole("button", { name: "Allow once", exact: true }).click());
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Codex heard accept");
    });

    await step("local-agents: declined with a note, Codex hears why; allowed in this chat, the same command runs unasked next time", async () => {
      let before = decisions().length;
      await askAndAnswer("build it again", async (card) => {
        await card.getByRole("button", { name: "Don't allow", exact: true }).click();
        await card.getByRole("textbox").fill("use make instead");
        await card.getByRole("button", { name: "Don't allow", exact: true }).click();
      });
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["decline"]), "Codex heard decline");
      assert(steers().some((s) => s.includes("use make instead")), `the note went with it: ${JSON.stringify(steers())}`);

      before = decisions().length;
      await askAndAnswer("one more build", (card) => card.getByRole("button", { name: "Allow in this chat", exact: true }).click());
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["acceptForSession"]), "allowed for the conversation");
      // the next turn asks the same command: answered by Gamma, no card
      before = decisions().length;
      await user.api("/api/local-agents/chat/turns", { method: "POST", body: { bucket: main.id, text: "and once more" } });
      await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 40000, what: "the turn ends unasked" });
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Gamma answered the granted command");
      const last = (await conversation()).messages.at(-1);
      assert(!last.steps.some((s) => s.status === "asked") && !(last.asks || []).length, "nothing was put to the person");
      await user.api("/api/local-agents/codex", { method: "PATCH", body: { runs: "never" } });
    });

    // Step 3c: the rest of the computer side.
    await step("local-agents: the passage chip names its source line, the strip's menu asks the shell, and a model, effort and speed chosen reach Codex as its reply is typed", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await recordShellAsks(page);
        await selectOnPdf(page, 1, "The penalty");
        const chip = dock.locator(".chatSelChips .chatSelChip").first();
        await until(async () => (await chip.innerText()).includes("sections/method.tex:3"), { what: "the chip names the passage's source line" });

        await dock.locator(".computerStripMenu").click();
        await page.locator(".ctxMenuItem", { hasText: "Continue in a terminal" }).click();
        await dock.locator(".computerStripMenu").click();
        await page.locator(".ctxMenuItem", { hasText: "Show in Explorer" }).click();
        // a message event comes after its post: wait for both
        const asks = await until(async () => {
          const got = await page.evaluate(() => window.shellAsks.map((a) => [a.type, a.page]));
          return got.length >= 2 ? got : null;
        }, { what: "both asks posted" });
        assertEq(JSON.stringify(asks), JSON.stringify([["continue-in-terminal", main.id], ["reveal-on-disk", main.id]]),
          "the page asks the shell with the page's id, nothing more");

        const modelChip = dock.locator(".chatModelChip .uiSelectBtn");
        await modelChip.click();
        const items = page.locator(".uiSelectMenu .ctxMenuItem");
        assert((await items.allInnerTexts()).some((s) => s.includes("GPT Fake Mini")) &&
          !(await items.allInnerTexts()).some((s) => s.includes("Hidden")), "the models Codex lists, not its hidden ones");
        await items.filter({ hasText: "GPT Fake Mini" }).click();
        await modelChip.click();
        const sections = page.locator(".uiSelectMenu .ctxMenuLabel");
        await until(async () => (await sections.allInnerTexts()).some((s) => /effort/i.test(s)), { what: "the menu's sections" });
        assert(!(await sections.allInnerTexts()).some((s) => /speed/i.test(s)), "a model with no other tier offers no speed");
        await items.filter({ hasText: /^\s*GPT Fake\s*$/ }).click();
        await modelChip.click();
        assert((await sections.allInnerTexts()).some((s) => /speed/i.test(s)) &&
          (await items.allInnerTexts()).some((s) => /Default \(standard\)/.test(s)), "its speeds, the default named");
        await items.filter({ hasText: /^\s*medium\s*$/i }).click();
        await modelChip.click();
        await items.filter({ hasText: /^\s*fast\s*$/i }).click();
        await until(async () => /GPT Fake · medium/i.test(await modelChip.innerText())
          && await dock.locator(".chatModelChip .chatSpeedGlyph").count() === 1, { what: "the chip shows the choice, the speed its glyph" });

        const replies = dock.locator(".chatBubble.ai");
        const earlier = await replies.count();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("say it slowly");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await until(async () => (await replies.count()) > earlier, { timeout: 10000, what: "the new turn's bubble" });
        // the reply grows: seen part-way before it is whole
        const seen = new Set();
        await until(async () => {
          const last = (await replies.nth(earlier).innerText().catch(() => "")).trim();
          if (last) seen.add(last);
          return last.includes(CHAT_REPLY);
        }, { timeout: 40000, every: 250, what: "the reply, whole" });
        await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 20000, what: "the turn ends" });
        const first = CHAT_REPLY.split(" ")[0];
        assert([...seen].some((s) => s.includes(first) && !s.includes(CHAT_REPLY)),
          `a part of the reply was seen before the whole: ${JSON.stringify([...seen])}`);
        assertEq(await dock.locator(".chatMsgModel .chatSpeedGlyph").count(), 1, "the reply's model row: the speed it ran at");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const thread = since.find((e) => ["thread/start", "thread/resume"].includes(e.in?.method))?.in.params;
      const turn = since.find((e) => e.in?.method === "turn/start")?.in.params;
      assertEq(JSON.stringify([thread?.model, turn?.effort, turn?.serviceTierForTurn]), JSON.stringify(["gpt-fake", "medium", "priority"]),
        "the model, the effort and the speed chosen");
    });

    // Step 2.8 of docs/research/agents-as-participants-plan.md: a chat with no AI service.
    await step("local-agents: with no AI service, a library PDF's chat is Codex's, the setup card picks it again, and it answers in the directory picked in the folder chip; a new chat starts there, and Add folder… offers and picks another", async () => {
      const notesDir = path.join(disk, "notes");
      fs.mkdirSync(path.join(notesDir, "sections"), { recursive: true });
      fs.writeFileSync(path.join(notesDir, "sections", "method.tex"), "We fit it by gradient descent.\n");
      const offered = await user.api("/api/local-agents/dirs", { method: "PUT", body: [{ path: notesDir, name: "Notes" }] });
      assertEq(offered.dirs.map((d) => d.name).join(","), "Notes", "the directory offered");
      const up = await user.upload("/api/uploads", makePdf([["A library paper for Codex"]]), "library.pdf", "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Library paper", source_url: up.source_url, original_filename: "library.pdf" } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        // no AI service connected: the chat's own agent is Codex, on a page of no synced folder
        await dock.locator(".computerStrip").waitFor();
        await dock.locator(".chatEmpty", { hasText: "Ask Codex anything" }).waitFor();
        // Gamma AI picked shows the setup card, which offers Codex beside the AI services
        await dock.locator(".chatModelChip .uiSelectBtn").click();
        await page.getByRole("menuitemradio", { name: "Gamma AI", exact: true }).click();
        const card = dock.locator(".chatSetup");
        await card.waitFor();
        await card.getByRole("button", { name: /^Codex/ }).click();
        await dock.locator(".computerStrip").waitFor();
        // the header keeps what applies to Codex's conversation
        await dock.getByRole("button", { name: "Chat history", exact: true }).waitFor();
        // where it works: the folder chip at the bar's start, a directory of its own while none was used
        // yet, the directory offered picked before the first turn
        const place = dock.locator(".chatPlaceChip .uiSelectBtn");
        assertEq((await place.innerText()).trim(), "Its own directory", "none used yet");
        await place.click();
        await page.getByRole("group", { name: "Works in" }).getByRole("menuitemradio", { name: "Notes", exact: true }).click();
        await dock.locator(".chatEmpty", { hasText: "it works in the directory you chose" }).waitFor();
        await until(async () => (await place.innerText()).trim() === "Notes", { what: "the chip names the pick" });
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("what is this paper about?");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        // the reply in the chat's own transcript: its steps pill, its text, what it changed
        await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor({ timeout: 40000 });
        await until(async () => (await dock.locator(".chatSteps").last().innerText()).trim() === "1 step · edited 1 file", { what: "the steps pill, done" });
        // outside a synced folder the changed file is named, not linked: no page holds it
        await dock.locator(".chatFileChanges code", { hasText: "sections/method.tex" }).waitFor();
        assertEq(await dock.locator(".chatFileChanges a").count(), 0, "no link to a file Gamma does not sync");
        // the chip shows where it works, the conversation's now and no longer to change; the strip leaves it to the chip
        await until(async () => (await place.getAttribute("title")) === "Codex works in Notes in this conversation: start a new chat to work elsewhere",
          { what: "the conversation's now" });
        assertEq([(await place.innerText()).trim(), await dock.locator(".computerPlace").count()].join(), "Notes,0", "named once, by the chip");
        await place.click();
        const kept = page.getByRole("group", { name: "Works in" }).getByRole("menuitemradio", { name: "Notes", exact: true });
        assertEq([await kept.getAttribute("aria-checked"), await kept.isDisabled()].join(), "true,true", "checked and disabled");
        assertEq(await page.getByRole("menuitem", { name: "Add folder…", exact: true }).count(), 0, "nothing to add to a conversation's place");
        await page.keyboard.press("Escape");
        const stored = (await user.api(`/api/local-agents/chat?bucket=${paper.id}`)).conversation;
        assertEq(stored.messages.at(-1).state, "done", "the turn ran");
        // a new chat starts in the folder the last one began in, as a Codex thread in the project used last
        await dock.getByRole("button", { name: "New chat", exact: true }).click();
        await until(async () => (await place.getAttribute("title")) === "Codex works in Notes", { what: "the new chat's, to choose" });
        await dock.locator(".chatEmpty", { hasText: "it works in the directory you chose" }).waitFor();
        // Add folder…: the shell's folder dialog (its ask recorded, its PUT made here as the shell makes it), the
        // folder it offers then picked
        await recordShellAsks(page);
        await place.click();
        await page.getByRole("menuitem", { name: "Add folder…", exact: true }).click();
        await until(async () => (await page.evaluate(() => window.shellAsks.map((a) => `${a.type}:${a.dir}`))).join() === "agent-dirs:new",
          { what: "the shell asked for a folder" });
        const draftsDir = path.join(disk, "drafts");
        fs.mkdirSync(draftsDir, { recursive: true });
        // listed before Notes, which the chip ranks first as the one used
        await user.api("/api/local-agents/dirs", { method: "PUT",
          body: [{ path: draftsDir, name: "" }, ...offered.dirs.map(({ id, path: at, name }) => ({ id, path: at, name }))] });
        await until(async () => (await place.innerText()).trim() === "drafts", { timeout: 10000, what: "the folder added, picked" });
        await place.click();
        const listed = page.getByRole("group", { name: "Works in" }).getByRole("menuitemradio");
        await until(async () => (await listed.count()) === 3, { what: "the menu, its three rows" });
        const rows = await listed.allInnerTexts();
        assertEq(rows.map((r) => r.trim()).join(), "Its own directory,Notes,drafts", "the folder used last before the others");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assert(fs.readFileSync(path.join(notesDir, "sections", "method.tex"), "utf8").includes("weight decay"), "Codex edited the directory offered");
      await user.api("/api/local-agents/dirs", { method: "PUT", body: [] });
    });

    // Step 2.7: Gamma's tools for the agent (docs/dev/local_agents.md "Gamma's tools").
    await step("local-agents: with no AI service, Codex reads a library PDF's highlight through Gamma's tools with its turn's token, gone when the turn ends", async () => {
      const quote = "Heliotrope blooms at dusk";
      const note = "the line for my talk";
      const up = await user.upload("/api/uploads", makePdf([[quote, "and closes at dawn"]]), "heliotrope.pdf", "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Heliotrope paper", source_url: up.source_url, original_filename: "heliotrope.pdf" } });
      const rect = { x1: 72, y1: 60, x2: 300, y2: 80 };
      await user.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: note, properties: {
        quote, color: "#ffe5648c", pdf_position: { pageNumber: 1, ...PAGE, boundingRect: rect, rects: [rect] } } } });
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await dock.locator(".computerStrip").waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(READ_ASK);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        // the answer quotes the highlight and its note, which only Gamma's tools gave it
        const reply = dock.locator(".chatBubble.ai", { hasText: "From Gamma:" });
        await reply.waitFor({ timeout: 40000 });
        await until(async () => { const said = await reply.innerText(); return said.includes(quote) && said.includes(note); },
          { what: "the reply quotes the highlight and its note" });
        // the transcript shows the call as a step, in the words Gamma AI's own call of the tool reads
        const pill = dock.locator(".chatSteps").last();
        await until(async () => (await pill.innerText()).trim() === "1 step · read 1 page", { what: "the steps pill, done" });
        await pill.click();
        await dock.locator(".chatToolAction", { hasText: "Read a page" }).waitFor();
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const told = since.find((e) => e.in?.method === "turn/start").in.params.input.map((p) => p.text || "").join("\n");
      assert(told.includes("## Gamma's tools") && told.includes(`\`read_page\` with \`page_id\` "${paper.id}"`),
        `the prompt points Codex to the page through the tools: ${told.slice(0, 800)}`);
      assert(!told.includes(note), "the prompt pastes none of the page's notes");
      // the turn's app-server, in its thread's directory (a probe's, in the server's temporary one, gets no token)
      const cwd = since.find((e) => e.in?.method === "thread/start").in.params.cwd;
      const appServer = since.find((e) => e.argv?.[0] === "app-server" && path.relative(e.cwd, cwd) === "");
      assert(appServer?.env.includes(TOKEN_ENV), `the turn's token reached the agent in its environment: ${JSON.stringify(appServer)}`);
      const called = since.find((e) => e.gamma === "read_page");
      assert(called && called.http === 200 && !called.error && called.text.includes(quote), `the call read the page: ${JSON.stringify(called)}`);
      const stored = (await user.api(`/api/local-agents/chat?bucket=${paper.id}`)).conversation;
      const turned = stored.messages.at(-1);
      assertEq(JSON.stringify([turned.state, turned.steps.map((s) => [s.kind, s.tool, s.status])]),
        JSON.stringify(["done", [["gamma", "read_page", "completed"]]]), "the turn and its step as stored");
      await until(async () => server.log().includes(`[agents] the token of ${turned.turn_id} deleted`),
        { what: "the server deleted the turn's token" });
    });

    // Step 3.3: the writing tools, through the same token.
    await step("local-agents: Codex comments on a PDF's passage through Gamma's tools: the thread reads Codex on this computer, for the person, and the passage's highlight names it", async () => {
      const up = await user.upload("/api/uploads", makePdf([[NOON, "and close at dusk"]]), "marigold.pdf", "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Marigold paper", source_url: up.source_url, original_filename: "marigold.pdf" } });
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await dock.locator(".computerStrip").waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(COMMENT_ASK);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.ai", { hasText: "Commented:" }).waitFor({ timeout: 40000 });
        const called = heard().slice(before).find((e) => e.gamma === "comment");
        assert(called && called.http === 200 && !called.error && called.text.includes(`ok — commented on page 1, “${NOON}”`),
          `the call commented: ${JSON.stringify(called)}`);
        const thread = (await threadsOf(paper.id)).find((th) => th.comments[0]?.body === "Is noon right here?");
        assert(thread, "the thread is on the page");
        const [comment] = thread.comments;
        assertEq(JSON.stringify([comment.author, comment.agent, comment.agent_kind]), JSON.stringify(["la-user", "Codex", "computer"]),
          "written for its account, by Codex");
        const computer = comment.agent_on;
        assert(computer, "the comment names the computer Codex ran on");
        // the highlight it made says who made it
        await page.locator(`[data-page="1"] [data-hl-id][title="Highlighted by Codex on ${computer}, for la-user"]`).first()
          .waitFor({ timeout: 10000 });
        await page.goto(`${at(paper.id)}&thread=${thread.id}`);
        const item = page.locator(".commentItem", { hasText: "Is noon right here?" }).first();
        await item.waitFor({ timeout: 10000 });
        assertEq(await item.locator(".commentAuthor").textContent(), "Codex", "the agent's name");
        assertEq(await item.locator(".commentFor").textContent(), `on ${computer}, for la-user`, "on its computer, for the person");
        assertEq(await item.locator(":scope > .agentAvatar").getAttribute("data-agent"), "computer", "a computer agent's avatar");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: a running turn is a Background tasks row, whose Stop ends it", async () => {
      await user.api("/api/local-agents/chat/turns", { method: "POST", body: { bucket: main.id, text: "take your time" } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        await page.click("button[aria-label='Background tasks']");
        const row = page.locator(".tasksPopover .taskRow", { hasText: "Codex on this computer" });
        await row.waitFor({ timeout: 15000 });
        await row.locator('button[aria-label^="Stop Codex on this computer"]').click();
        await until(async () => (await conversation()).messages.at(-1).state === "stopped", { timeout: 20000, what: "the turn stops" });
        await row.waitFor({ state: "detached", timeout: 15000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: History lists the sessions Codex keeps of the folder on this computer: a terminal's opens as the chat's conversation, the next message resumes it, and one open in another window says why", async () => {
      // another Codex has the held thread open: its writer's lock, as Codex holds it
      const holder = spawn(PYTHON, [path.join(FAKES, "hold_lock.py"), path.join(codexHome, "thread-writer-locks", `${HELD.id}.lock`)]);
      await new Promise((resolve, reject) => { holder.stdout.once("data", resolve); holder.once("error", reject); });
      const before = heard().length;
      const computer = (await user.api("/api/local-agents")).computer;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        const history = async () => {
          await dock.getByRole("button", { name: "Chat history", exact: true }).click();
          const pop = page.locator(".chatHistoryPop");
          // the caption names the CLI and the directory; this computer's are not said to be on it
          const caption = pop.locator(".popoverSection", { hasText: "Codex sessions in " });
          await caption.waitFor();
          assert(!computer || !(await caption.innerText()).includes(computer), `this computer goes unnamed: ${await caption.innerText()}`);
          return pop;
        };
        let pop = await history();
        const terminal = pop.locator(".chatHistRow", { hasText: TERMINAL.name });
        await terminal.waitFor({ timeout: 20000 });
        assertEq((await terminal.locator(".chatHistBy").innerText()).trim(), "Terminal", "started in a terminal");
        const held = pop.locator(".chatHistRow", { hasText: HELD.name });
        const why = "Codex has this session open in another window: close it there first.";
        assertEq(await held.getAttribute("aria-disabled"), "true", "the held one cannot be opened");
        assertEq(await held.getAttribute("title"), why, "and says why");
        assertEq((await held.locator(".chatHistDetail").innerText()).trim(), why, "under its title too");
        // a press still lands on a row that is aria-disabled (Playwright waits for enabled, a person's click does not)
        await held.click({ force: true });
        await page.locator(".statusPill", { hasText: why }).waitFor({ timeout: 3000 });
        // the caption's refresh reads them again in place
        await pop.getByRole("button", { name: "Read the sessions again", exact: true }).click();
        await terminal.waitFor();
        await terminal.click();
        await pop.waitFor({ state: "detached" });
        await dock.locator(".chatBubble.user", { hasText: TERMINAL.ask }).waitFor({ timeout: 15000 });
        await dock.locator(".chatBubble.ai", { hasText: TERMINAL.reply }).waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(SESSION_ASK);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.ai", { hasText: SESSION_REPLY }).waitFor({ timeout: 40000 });
        pop = await history();
        const ours = pop.locator(".chatHistRow", { hasText: TERMINAL.name }).filter({ has: page.locator(".chatHistBy", { hasText: "Continued here" }) });
        await ours.waitFor({ timeout: 20000 });
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally {
        await ctx.close();
        holder.stdin.end();
      }
      const since = heard().slice(before);
      assertEq(JSON.stringify(since.filter((e) => e.in?.method === "thread/resume").map((e) => e.in.params.threadId)),
        JSON.stringify([TERMINAL.id]), "the next turn resumed the terminal's thread");
      assertEq(since.filter((e) => e.in?.method === "thread/start").length, 0, "no thread started in its place");
      const prompt = since.find((e) => e.in?.method === "turn/start").in.params.input.map((p) => p.text || "").join("\n");
      assert(!prompt.includes(TERMINAL.ask), "the terminal's history stays the CLI's: the prompt does not repeat it");
      const stored = await conversation();
      assertEq(stored.title, TERMINAL.name, "the conversation is titled as the session is");
    });

    await step("local-agents: turned off by its owner, the folder has no agent", async () => {
      const off = await user.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: false } });
      assertEq(off.enabled, false, "off");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent ?? null, null, "no agent in the vault");
      await user.api(`/api/folder-links/${link.id}`, { method: "DELETE" });
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

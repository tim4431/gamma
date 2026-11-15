import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// An agent on this computer answering a Send (docs/dev/local_agents.md): the
// desktop app's own server, where Gamma itself starts Codex in a work
// folder's directory with the comments its owner sent. The vaults recipe
// (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent) with local
// agents on and the stand-ins of backend/tests/fake_agents/ in place of the
// CLIs, so no real agent ever runs: the SyncTeX sample paper as a work
// folder, two comments on its PDF, Codex turned on for the computer by the
// vault's maker, the Feedback dialog naming Codex before and after, Send, the
// request worked and done with what changed, the replies signed Codex on
// the person's computer and the threads resolved; a reply ticked Send to
// Codex (by its @codex) a turn of its thread, its chip, its answer under its
// name, Open in chat and the next ticked reply resuming the session; and the
// agent turned off.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAGE = { width: 612, height: 792 };
const SUMMARY = "Moved the legend and introduced the penalty's weight.";
const CHAT_REPLY = "Tightened the sentence in sections/method.tex:3.";
const BUILD = "latexmk -pdf main.tex";
// Gamma's tools (step 2.7): the variable that alone carries a turn's token
// (gamma/local_agents/events.py TOKEN_ENV), and the message whose turn
// reads the page through them and quotes what it read.
const TOKEN_ENV = "GAMMA_AGENT_MCP_TOKEN";
const READ_ASK = "what did I highlight?";
// 3.3: this message's turn comments on a passage of its page through them.
const COMMENT_ASK = "ask the authors about noon";
const NOON = "Marigolds open at noon";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function localAgentScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, makePdf } = env;
  if (!wanted("local-agents")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-agents-"));
  const agentLog = path.join(disk, "agent.jsonl");
  // Both agents are the stand-ins, so neither probe reaches a CLI of this
  // computer. Codex answers every comment it is handed as fixed, after an
  // edit of the paper's method and a pause long enough to be seen working;
  // each new session is a thread of its own id, so a resumed one shows.
  const server = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    GAMMA_AGENT_CLAUDE: JSON.stringify([PYTHON, path.join(FAKES, "fake_claude.py")]),
    FAKE_AGENT: JSON.stringify({ each: "fixed", summary: SUMMARY, delay: 8, log: agentLog, distinct: true, chat: CHAT_REPLY,
      // a turn of a folder that lets it ask (3b) asks to run the build with the network
      ask: [{ command: BUILD, reason: "The build fetches a font", host: "fonts.example" }],
      // a chat turn's reply comes as it is typed (3c): a word every half second
      typing: 0.5,
      edits: [{ path: "sections/method.tex", old: "by gradient descent.", new: "by gradient descent with weight decay $\\lambda$." }],
      // 2.7: this message's turn reads its page with Gamma's tools and answers with what it read
      turns: { [READ_ASK]: { gamma: [{ tool: "read_page", arguments: { page_id: "$page" } }], edits: [], delay: 0, typing: 0,
        chat: "From Gamma:\n\n{gamma}" },
      [COMMENT_ASK]: { gamma: [{ tool: "comment", arguments: { page_id: "$page", quote: NOON, pdf_page: 1, body: "Is noon right here?" } }],
        edits: [], delay: 0, typing: 0, chat: "Commented:\n\n{gamma}" } } }),
  } });
  try {
    await server.start();
    await server.manage("create-user", "la-user", "la-pw");
    const user = await new Account(server, "la-user", "la-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const desktop = () => user.context(browser, { userAgent: DESKTOP_UA });
    // written as an editor that finished a while ago, so a round takes it at once
    const paperDir = path.join(disk, "paper");
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      const file = path.join(paperDir, rel);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, fs.readFileSync(path.join(PAPER, "v1", rel)));
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    }
    const method = path.join(paperDir, "sections", "method.tex");
    const threadsOf = async (page) => (await user.api(`/api/pages/${page}/comments?state=all`)).threads;
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
    const openFeedback = async (page) => {
      await openComments(page);
      await page.locator(".dockWindow:has(.commentsPanel)").getByRole("button", { name: "Feedback", exact: true }).click();
      const d = page.getByRole("dialog", { name: "Feedback", exact: true });
      await d.locator(".feedbackEntry").nth(1).waitFor({ timeout: 10000 });
      return d;
    };
    const notes = async (d) => (await d.locator(".feedbackAgentNote").allInnerTexts()).map((s) => s.trim());
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
    // The agents the composer's chip lists, by name, its menu closed again.
    const agentsListed = async (page, dock) => {
      await dock.locator(".chatModelChip .uiSelectBtn").click();
      const names = await page.locator(".uiSelectMenu [role=group]").first().getByRole("menuitemradio").allInnerTexts();
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
    const threads = [];
    await step("local-agents: a work folder of the paper with two comments on its PDF, and Codex found on this computer", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      main = await until(async () => (await user.api(`/api/folders/${link.folder_id}/pages?limit=500`)).pages
        .find((p) => p.properties?.file_name === "main.pdf"), { timeout: 30000, what: "main.pdf's page" });
      for (const [place, body] of [[PLACES.v1.figure.rect, "move the legend inside the plot"], [PLACES.v1.equation.rect, "lambda is never introduced"]]) {
        const area = await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: "",
          properties: { pdf_position: areaOf(place), color: "rgba(255, 226, 143, 0.65)" } } });
        threads.push((await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: area.id, body } })).thread);
      }
      const found = (await user.api("/api/local-agents?fresh=1")).agents;
      assertEq(found.map((a) => a.name).join(","), "codex,claude", "the agents Gamma knows");
      const codex = found.find((a) => a.name === "codex");
      assert(codex.found && codex.ready, `Codex is found and signed in: ${JSON.stringify(codex)}`);
      assertEq(codex.label, "Codex");
      assertEq(link.agent ?? null, null, "the folder has no agent yet");
    });

    await step("local-agents: with no agent on, the Feedback dialog sends to an agent the person runs and says where to turn one on", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const d = await openFeedback(page);
        await d.getByRole("button", { name: "Send 2 comments", exact: true }).waitFor();
        // the folder is kept here and an agent was found: the line offering one
        await until(async () => (await notes(d)).some((s) => s === "To have Codex or Claude Code answer here, turn one on in Settings → AI."),
          { timeout: 15000, what: "the line saying where to turn an agent on" });
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: the vault's maker turns Codex on for the computer, which nothing waits on yet", async () => {
      const turned = await user.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: true } });
      assertEq(turned.queued, 0, "no Send was waiting");
      assertEq(JSON.stringify([turned.name, turned.label, turned.enabled, turned.runs, turned.mine]),
        JSON.stringify(["codex", "Codex", true, "never", true]), "on, the account's own");
      assertEq(turned.ready, true, "ready, from the probe the PATCH ran");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent?.name, "codex", "the listing carries the agent that works in the vault");
      assertEq(listed.agent.running, null, "nothing running");
      const doc = await user.api(`/api/annotations?scope=${link.folder_id}`);
      assertEq(JSON.stringify([doc.agent?.name, doc.agent?.takes, doc.agent?.ready]), JSON.stringify(["codex", true, true]), "the feedback document's agent takes the maker's Sends");
    });

    let request = null;
    await step("local-agents: Send to Codex from the Feedback dialog, which shows it working and then done with what it changed", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const d = await openFeedback(page);
        const send = d.getByRole("button", { name: "Send 2 comments to Codex", exact: true });
        await send.waitFor({ timeout: 10000 });
        assert((await notes(d)).includes("Codex works on this computer, in this folder's directory, and answers each comment."), "the hint under the note");
        await d.getByRole("textbox", { name: "Instruction for the agent (optional)" }).fill("fix these");
        await send.click();
        const row = d.locator(".feedbackRequest");
        await row.waitFor({ timeout: 10000 });
        const state = row.locator(".feedbackRequestState");
        await until(async () => (await state.innerText()).trim() === "Codex is working…", { timeout: 20000, what: "the request reads Codex is working…" });
        await until(async () => (await state.innerText()).trim() === "Done by Codex", { timeout: 40000, what: "the request reads Done by Codex" });
        // the agent's own words on a line of their own, under the request's
        const done = (await row.locator(".feedbackRequestDetail").innerText()).trim();
        assert(done.includes(SUMMARY), `the request's detail has the summary: ${done}`);
        // the file the comment's source line names is named; the others are counted
        assert(done.includes("Changed sections/method.tex."), `and the file changed: ${done}`);
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const listed = await user.api("/api/feedback/requests?after=0");
      assertEq(listed.requests.length, 1, "one request");
      request = listed.requests[0];
      assertEq(JSON.stringify([request.state, request.state_by, request.note]), JSON.stringify(["done", "Codex", "fix these"]), "done by Codex");
      assert(fs.readFileSync(method, "utf8").includes("by gradient descent with weight decay $\\lambda$."), "the edit is in the directory");
      // the agent ran in the folder's directory, told of both threads, and
      // got none of the server's own variables
      const seen = fs.readFileSync(agentLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
      const servers = seen.filter((e) => e.argv?.[0] === "app-server");
      assert(servers.some((e) => path.relative(e.cwd, paperDir) === ""), `an app-server ran in the work folder: ${JSON.stringify(servers)}`);
      assert(seen.every((e) => !e.env || e.env.every((name) => !name.startsWith("GAMMA_") || name === TOKEN_ENV)),
        "no GAMMA_ variable but the turn's token reached the agent");
      const turn = seen.find((m) => m.in?.method === "turn/start").in.params;
      const prompt = turn.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(threads.every((th) => prompt.includes(`"thread_id": "${th.id}"`)), "the prompt names both threads");
      assertEq(turn.sandboxPolicy?.networkAccess, false, "a sandbox without network");
    });

    await step("local-agents: Codex's replies are signed Codex on this computer, for the person, and the threads it fixed are resolved", async () => {
      const stored = await threadsOf(main.id);
      let computer = "";
      for (const th of threads) {
        const got = stored.find((s) => s.id === th.id);
        assert(got?.resolved_at, `${th.id} is resolved`);
        const reply = got.comments[1];
        assertEq(JSON.stringify([reply?.agent, reply?.agent_kind, reply?.author, reply?.body]),
          JSON.stringify(["Codex", "computer", "la-user", `Done for ${th.id}.`]), "the reply");
        computer = reply.agent_on;
      }
      assert(computer, "the reply names the computer Codex ran on");
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const panel = await openComments(page);
        await page.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Resolved (2)" }).click();
        const row = panel.locator(`.commentsPanelRow[data-thread-id="${threads[0].id}"]`);
        await row.waitFor();
        await row.click();
        const reply = page.locator(".commentThread .commentItem.reply", { has: page.locator(".commentAuthor", { hasText: "Codex" }) }).first();
        await reply.waitFor({ timeout: 10000 });
        const byline = `Written by Codex on ${computer}, for la-user`;
        assertEq(await reply.locator(".commentAuthor").getAttribute("title"), byline, "the byline names the computer and the person");
        assertEq(await reply.locator(".commentFor").textContent(), `on ${computer}, for la-user`, "after the agent's name");
        assertEq(await reply.locator(":scope > .agentAvatar").getAttribute("title"), byline, "and so does the agent's avatar");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    // Step 3.5 of docs/research/agents-as-participants-plan.md: a comment sent to
    // an agent (docs/dev/local_agents.md "A comment sent to an agent").
    let caption = null, captionTurn = "";
    await step("local-agents: @codex in a reply ticks Send to Codex, and the reply is a turn of its thread: the chip says Codex is working, then its reply comes under its name", async () => {
      caption = await commentOn(PLACES.v1.figure.rect, "the caption says 2025");
      const before = turnsHeard().length;
      const sends = (await user.api("/api/feedback/requests?after=0")).requests.length;
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
      assertEq((await user.api("/api/feedback/requests?after=0")).requests.length, sends, "no Send was made");
      const turns = turnsHeard().slice(before);
      assertEq(turns.length, 1, "one turn");
      captionTurn = turns[0];
      assert(!turnsHeard().slice(0, before).includes(captionTurn), `a session of its own: ${captionTurn}`);
      const told = heard().filter((e) => e.in?.method === "turn/start").at(-1).in.params.input.map((p) => p.text || "").join("\n");
      assert(told.includes('"message": "fix this only"') && told.includes(`"thread_id": "${caption.id}"`)
        && told.includes("the caption says 2025"), `the comment and its thread reach Codex: ${told.slice(0, 600)}`);
    });

    await step("local-agents: in the thread Codex answered last the tick starts ticked, the field asks Codex to edit, and the reply resumes the session", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id);
        await replyField(thread);
        assertEq(await thread.locator(".commentSendTo input[type=checkbox]").isChecked(), true, "the agent spoke last: ticked");
        assertEq((await thread.locator(".commentField .cm-placeholder").last().innerText()).trim(), "Ask Codex to edit");
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
        await until(async () => (await page.textContent(".statusPill").catch(() => "") || "")
          .includes("Reply to ask Codex again: only the newest comment is sent."), { what: "the status says why it was not sent" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const codex = (await user.api("/api/local-agents")).agents.find((a) => a.name === "codex");
      await user.api(`/api/comments/${caption.id}/resolve`, { method: "POST", body: { resolved: true } });
      const refused = await user.api(`/api/pages/${main.id}/comments`, { method: "POST",
        body: { block_id: caption.block_id, thread_id: caption.id, body: "once more", to: codex.id } });
      assertEq(refused.turn?.refused, "closed", "a resolved thread is reopened first");
      assertEq(refused.comment.body, "once more", "the comment is stored all the same");
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
        await until(async () => (await dock.locator(".computerStrip").innerText()).includes("Codex is working"),
          { timeout: 15000, what: "the strip says Codex is working" });
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
      assert(!turns[0].in.params.outputSchema, "a chat turn answers in prose, with no answer schema");
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
    await step("local-agents: the passage chip names its source line, the strip's menu asks the shell, and a model and effort chosen reach Codex as its reply is typed", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        // what the page asks the desktop shell (no shell here: the asks are recorded)
        await page.evaluate(() => {
          window.shellAsks = [];
          window.addEventListener("message", (e) => { if (e.data?.source === "gamma-app") window.shellAsks.push(e.data); });
        });
        await selectOnPdf(page, 1, "The penalty");
        const chip = dock.locator(".chatSelChips .chatSelChip").first();
        await until(async () => (await chip.innerText()).includes("sections/method.tex:3"), { what: "the chip names the passage's source line" });

        await dock.locator(".computerStripMenu").click();
        await page.locator(".ctxMenuItem", { hasText: "Continue in a terminal" }).click();
        await dock.locator(".computerStripMenu").click();
        await page.locator(".ctxMenuItem", { hasText: "Show in folder" }).click();
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
        await page.locator(".uiSelectMenu .ctxMenuItem").filter({ hasText: /^\s*medium\s*$/i }).click();
        await until(async () => /GPT Fake Mini · medium/i.test(await modelChip.innerText()), { what: "the chip shows the choice" });

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
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const thread = since.find((e) => ["thread/start", "thread/resume"].includes(e.in?.method))?.in.params;
      const turn = since.find((e) => e.in?.method === "turn/start")?.in.params;
      assertEq(JSON.stringify([thread?.model, turn?.effort]), JSON.stringify(["gpt-fake-mini", "medium"]), "the model and the effort chosen");
    });

    // Step 2.8 of docs/research/agents-as-participants-plan.md: a chat with no AI service.
    await step("local-agents: with no AI service, a library PDF's chat is Codex's, the setup card picks it again, and it answers in a directory offered", async () => {
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
        // where it works: the directory offered, picked in the strip before the first turn
        await dock.locator(".computerPlace .uiSelectBtn").click();
        await page.getByRole("menuitemradio", { name: "Notes", exact: true }).click();
        await dock.locator(".chatEmpty", { hasText: "it works in the directory you chose" }).waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("what is this paper about?");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        // the reply in the chat's own transcript: its steps pill, its text, what it changed
        await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor({ timeout: 40000 });
        await until(async () => (await dock.locator(".chatSteps").last().innerText()).trim() === "1 step", { what: "the steps pill, done" });
        await dock.locator(".chatFileChanges", { hasText: "sections/method.tex" }).waitFor();
        await until(async () => (await dock.locator(".computerPlace").innerText()).trim() === "Notes", { what: "the strip names the directory" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const stored = (await user.api(`/api/local-agents/chat?bucket=${paper.id}`)).conversation;
      assertEq(stored.messages.at(-1).state, "done", "the turn ran");
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
        // the transcript shows the call as a step
        const pill = dock.locator(".chatSteps").last();
        await until(async () => (await pill.innerText()).trim() === "1 step", { what: "the steps pill, done" });
        await pill.click();
        await dock.locator(".chatToolAction", { hasText: "Read Gamma: read_page" }).waitFor();
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

    await step("local-agents: turned off by its owner, the folder has no agent and the feedback document names none", async () => {
      const off = await user.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: false } });
      assertEq(off.enabled, false, "off");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent ?? null, null, "no agent in the vault");
      const doc = await user.api(`/api/annotations?scope=${link.folder_id}`);
      assertEq(doc.agent, null, "the document says none");
      await user.api(`/api/folder-links/${link.id}`, { method: "DELETE" });
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

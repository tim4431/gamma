import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// An agent on this computer answering a Send (docs/dev/local_agents.md): the
// desktop app's own server, where Gamma itself starts Codex in a work
// folder's directory with the comments its maker sent. The vaults recipe
// (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent) with local
// agents on and the stand-ins of backend/tests/fake_agents/ in place of the
// CLIs, so no real agent ever runs: the SyncTeX sample paper as a work
// folder, two comments on its PDF, Codex turned on for the folder by its
// maker, the Feedback dialog naming Codex before and after, Send, the
// request worked and done with what changed, the replies signed Codex on
// the person's computer and the threads resolved, and the agent turned off.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAGE = { width: 612, height: 792 };
const SUMMARY = "Moved the legend and introduced the penalty's weight.";
const CHAT_REPLY = "Tightened the sentence in sections/method.tex:3.";
const BUILD = "latexmk -pdf main.tex";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function localAgentScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
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
      edits: [{ path: "sections/method.tex", old: "by gradient descent.", new: "by gradient descent with weight decay $\\lambda$." }] }),
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
      assert(!("send" in made), "a comment that names no agent sends nothing");
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
    // The chat's Codex side, its strip saying the agent is there.
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".segGroup").getByRole("button", { name: "Codex", exact: true }).click();
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
    const conversation = async () => (await user.api(`/api/local-agents/chat?page=${main.id}`)).conversation;
    // The request a mention made, once it is done.
    const doneRequest = (id) => until(async () => {
      const found = (await user.api("/api/feedback/requests?after=0")).requests.find((r) => r.id === id);
      return found?.state === "done" ? found : null;
    }, { timeout: 40000, what: `request ${id} done` });

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
        await until(async () => (await notes(d)).some((s) => s.startsWith("To have Codex or Claude Code answer here, turn one on in the sync panel")),
          { timeout: 15000, what: "the line saying where to turn an agent on" });
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: the vault's maker turns Codex on for the work folder, which nothing waits on yet", async () => {
      const turned = await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "codex" } });
      assertEq(turned.queued, 0, "no Send was waiting");
      assertEq(turned.agent?.name, "codex", "the vault's agent");
      assertEq(turned.agent.label, "Codex");
      assertEq(turned.agent.ready, true, "ready, from the probe the PATCH ran");
      assertEq(turned.agent.running, null, "nothing running");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent?.name, "codex", "the listing carries it");
      const doc = await user.api(`/api/feedback?scope=${link.folder_id}`);
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
        await until(async () => (await state.innerText()).trim() === "Codex is working", { timeout: 20000, what: "the request reads Codex is working" });
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
      assert(seen.every((e) => !e.env || !e.env.some((name) => name.startsWith("GAMMA_"))), "no GAMMA_ variable reached the agent");
      const turn = seen.find((m) => m.in?.method === "turn/start").in.params;
      const prompt = turn.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(threads.every((th) => prompt.includes(`"thread_id": "${th.id}"`)), "the prompt names both threads");
      assertEq(turn.sandboxPolicy?.networkAccess, false, "a sandbox without network");
    });

    await step("local-agents: Codex's replies are signed Codex on the person's computer and the threads it fixed are resolved", async () => {
      const stored = await threadsOf(main.id);
      for (const th of threads) {
        const got = stored.find((s) => s.id === th.id);
        assert(got?.resolved_at, `${th.id} is resolved`);
        const reply = got.comments[1];
        assertEq(JSON.stringify([reply?.agent, reply?.agent_kind, reply?.author, reply?.body]),
          JSON.stringify(["Codex", "local", "la-user", `Done for ${th.id}.`]), "the reply");
      }
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
        const byline = "Written by Codex on la-user's computer";
        assertEq(await reply.locator(".commentAuthor").getAttribute("title"), byline, "the byline names the computer");
        assertEq(await reply.locator(":scope > [title]").first().getAttribute("title"), byline, "and so does the avatar");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    // Step 2: @codex in a comment (docs/dev/local_agents.md "A mention in a comment").
    let caption = null, captionTurn = "";
    await step("local-agents: in the maker's reply on the PDF the @ menu lists Codex on this computer, and the reply sends that thread to Codex", async () => {
      caption = await commentOn(PLACES.v1.figure.rect, "the caption says 2025");
      const before = turnsHeard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id);
        await replyField(thread);
        await page.keyboard.type("@cod");
        const menu = page.locator(".mentionMenu");
        const row = menu.locator(".refPopupItem.selected");
        await until(async () => (await row.locator(".refPopupText").innerText().catch(() => "")).startsWith("Codex"),
          { what: "the @ menu offers the folder's agent" });
        assertEq((await row.locator(".mentionAside").innerText()).trim(), "on this computer", "where it runs");
        assertEq(await menu.locator(".refPopupHead").allInnerTexts().then((h) => h.join("|")), "People", "under People");
        await page.keyboard.press("Enter");
        await menu.waitFor({ state: "detached" });
        await thread.locator(".cmMentionChip", { hasText: "@codex" }).waitFor();
        await page.keyboard.type("fix this only");
        assertEq((await thread.locator("[data-agent-hint]").innerText()).trim(), "Sends this thread to Codex on this computer.",
          "the field says what sending does");
        await page.keyboard.press("Shift+Enter");
        // the reply is stored, and its Send is the thread's alone
        const reply = await until(async () => (await threadsOf(main.id)).find((th) => th.id === caption.id)
          ?.comments.find((c) => c.body === "@codex fix this only"), { what: "the reply is stored" });
        const request = await doneRequest(reply.id);
        assertEq(JSON.stringify([request.note, request.threads.map((t) => t.id), request.state_by]),
          JSON.stringify(["fix this only", [caption.id], "Codex"]), "a Send of the thread, the comment's other words its instruction");
        // the reply's own @codex is the agent's chip in the maker's comment
        await thread.locator(".commentItem.reply .mentionChip.mention-agent", { hasText: "@codex" }).first().waitFor();
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const stored = (await threadsOf(main.id)).find((th) => th.id === caption.id);
      assert(stored.resolved_at, "the fixed thread is resolved");
      assertEq(JSON.stringify([stored.comments.at(-1).agent, stored.comments.at(-1).body]),
        JSON.stringify(["Codex", `Done for ${caption.id}.`]), "Codex answered it");
      const turns = turnsHeard().slice(before);
      assertEq(turns.length, 1, "one turn");
      captionTurn = turns[0];
      assert(!turnsHeard().slice(0, before).includes(captionTurn), `a session of its own: ${captionTurn}`);
    });

    await step("local-agents: a follow-up in the reopened thread resumes the session that answered there", async () => {
      const before = heard().length;
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id, true);
        await thread.getByRole("button", { name: "Reopen", exact: true }).click();
        await until(async () => !(await threadsOf(main.id)).find((th) => th.id === caption.id)?.resolved_at, { what: "the thread is open again" });
        await replyField(thread);
        await page.keyboard.type("@codex");
        await page.locator(".mentionMenu .refPopupItem.selected", { hasText: "Codex" }).waitFor();
        await page.keyboard.press("Enter"); // the row writes "@codex "
        await page.keyboard.type("keep the British spelling");
        await page.keyboard.press("Shift+Enter");
        const reply = await until(async () => (await threadsOf(main.id)).find((th) => th.id === caption.id)
          ?.comments.find((c) => c.body === "@codex keep the British spelling"), { what: "the follow-up is stored" });
        const request = await doneRequest(reply.id);
        assertEq(request.note, "keep the British spelling");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const resumed = since.filter((e) => e.in?.method === "thread/resume").map((e) => e.in.params.threadId);
      assertEq(JSON.stringify(resumed), JSON.stringify([captionTurn]), "Codex's thread/resume of the session that answered");
      assertEq(since.filter((e) => e.in?.method === "thread/start").length, 0, "no new session");
      assertEq(JSON.stringify(since.filter((e) => e.in?.method === "turn/start").map((e) => e.in.params.threadId)),
        JSON.stringify([captionTurn]), "the turn ran in it");
    });

    await step("local-agents: an edit of an older comment is not sent and says why; a mention in the page's own thread sends the file's open comments", async () => {
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
        assertEq((await first.locator("[data-agent-hint]").innerText()).trim(), "Mention Codex in a new reply to send this thread again.",
          "the field says before saving that an older comment's edit is not sent");
        await first.getByRole("button", { name: "Save", exact: true }).click();
        await until(async () => (await page.textContent(".statusPill").catch(() => "") || "")
          .includes("Mention Codex in a new reply to send this thread again."), { what: "the status says why it was not sent" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assertEq((await user.api("/api/feedback/requests?after=0")).requests.filter((r) => r.threads.some((t) => t.id === axis.id)).length,
        0, "no Send of that thread");
      const made = await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: main.id, body: "@codex tidy these up" } });
      assertEq(JSON.stringify([made.send?.agent, made.send?.scope, made.send?.threads]), JSON.stringify(["Codex", "file", 2]),
        "the file's open comments: the axis thread and the page's own");
      assertEq(made.thread.sent?.request_id, made.send.request_id, "the answer's thread carries its Sent chip");
      const request = await doneRequest(made.send.request_id);
      assertEq(JSON.stringify(request.threads.map((t) => t.id).sort()), JSON.stringify([axis.id, made.thread.id].sort()));
      const refused = await user.api(`/api/pages/${main.id}/comments`, { method: "POST",
        body: { block_id: caption.block_id, thread_id: caption.id, body: "@codex once more" } });
      assertEq(refused.send?.refused, "closed", "a resolved thread is reopened first");
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
        assertEq(await dock.locator(".segGroup button").allInnerTexts().then((b) => b.map((s) => s.trim()).join("|")), "Gamma AI|Codex",
          "the switch names Gamma AI and the folder's agent");
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
      assert(prompt.includes('"message": "tighten this sentence"') && prompt.includes("The penalty") && prompt.includes('"file": "main.pdf"'),
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
        await dock.locator(".computerEnded", { hasText: "Stopped" }).waitFor({ timeout: 20000 });
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
      assert(await user.api("/api/local-agents/chat/history?page=" + main.id).then((h) => h.conversations.length === 0),
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
        const card = dock.locator(".computerAsk");
        await card.waitFor({ timeout: 20000 });
        assertEq((await card.locator(".chatApprovalHead").innerText()).trim(), "Codex wants to run a command");
        assertEq((await card.locator(".computerAskCommand code").innerText()).trim(), BUILD, "the command, whole");
        assert((await card.innerText()).includes("The build fetches a font"), "its reason");
        assert((await card.innerText()).includes("It runs as you, with your files and the network."), "what allowing means");
        await until(async () => (await dock.locator(".computerStrip.asking").innerText().catch(() => "")).trim()
          === "Waiting for you: Codex wants to run a command", { what: "the strip waits for the person" });
        await answer(card);
        await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 40000, what: "the turn ends" });
        const replies = (await conversation()).messages.filter((m) => m.role === "agent" && m.text === CHAT_REPLY).length;
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === replies,
          { what: "the turn's reply in the window" });
        assertEq(await dock.locator(".computerAsk").count(), 0, "no card left");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    };

    await step("local-agents: the maker lets Codex ask in the chat, and a command it asks to run waits on a card until allowed once", async () => {
      const turned = await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent_runs: "ask" } });
      assertEq(turned.agent?.runs, "ask", "the folder lets its agent ask");
      const before = decisions().length;
      await askAndAnswer("build it", (card) => card.getByRole("button", { name: "Allow once", exact: true }).click());
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Codex heard accept");
    });

    await step("local-agents: declined with a note, Codex hears why; allowed in this chat, the same command runs unasked next time", async () => {
      let before = decisions().length;
      await askAndAnswer("build it again", async (card) => {
        await card.getByRole("button", { name: "Decline…", exact: true }).click();
        await card.getByRole("textbox").fill("use make instead");
        await card.getByRole("button", { name: "Decline", exact: true }).click();
      });
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["decline"]), "Codex heard decline");
      assert(steers().some((s) => s.includes("use make instead")), `the note went with it: ${JSON.stringify(steers())}`);

      before = decisions().length;
      await askAndAnswer("one more build", (card) => card.getByRole("button", { name: "Allow in this chat", exact: true }).click());
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["acceptForSession"]), "allowed for the conversation");
      // the next turn asks the same command: answered by Gamma, no card
      before = decisions().length;
      await user.api("/api/local-agents/chat/turns", { method: "POST", body: { page: main.id, text: "and once more" } });
      await until(async () => (await conversation()).messages.at(-1).state === "done", { timeout: 40000, what: "the turn ends unasked" });
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Gamma answered the granted command");
      const last = (await conversation()).messages.at(-1);
      assert(!last.steps.some((s) => s.status === "asked") && !(last.asks || []).length, "nothing was put to the person");
      await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent_runs: "never" } });
    });

    await step("local-agents: turned off by its maker, the folder has no agent and the feedback document names none", async () => {
      const off = await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "" } });
      assertEq(off.agent ?? null, null, "no agent");
      const doc = await user.api(`/api/feedback?scope=${link.folder_id}`);
      assertEq(doc.agent, null, "the document says none");
      await user.api(`/api/folder-links/${link.id}`, { method: "DELETE" });
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

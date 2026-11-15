import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// The chat's computer side on another server's page (step 4d of
// docs/research/agent-collab.md; docs/dev/local_agents.md "A work folder of
// another server"): two Gammas, a NAS (a plain server, its grace before a
// computer reads offline cut to 3 s) and a computer (the desktop app's own
// server: the vaults recipe of GAMMA_FOLDER_LINKS with no shell secret,
// local agents on, the stand-in Codex of backend/tests/fake_agents/ found
// and Claude Code not). On the NAS a shared
// workspace holds the folder "Paper", its maker alice and an editor, bob; the
// computer's own account makes a work folder of it from the SyncTeX sample
// paper's directory, shows the computer there and turns Codex on for it.
// Alice's plain browser on the NAS then lists "Codex on <host>" beside Gamma AI
// in the PDF page's chat's chip, its strip naming Codex on the computer: a message with a
// PDF passage runs on the computer and its reply and steps come back into the
// conversation the NAS keeps; a tab closed mid-turn finds the turn again and
// the follow-up resumes Codex's session there; Stop of a running turn ends
// it; New chat and History work as locally. An @codex in alice's reply on
// the NAS ticks Send to Codex and is a turn of its thread, which the
// computer takes over its channel, the thread's region's picture fetched
// for the prompt, and the NAS answers in the thread. Bob has no Codex side, until
// the computer's owner lets him use Codex there (a co-author, step 3.6 of
// docs/research/agents-as-participants-plan.md): his @codex in a comment on
// the paper then runs on the computer, and the answer in his thread reads
// "Codex on <host>, for rc-bob", alice's (the channel's account) named as
// its owner. A library PDF of the NAS in no synced folder (step 4.2 of the
// plan): alice picks Codex on the computer, whose turn runs there in a
// directory of its own and comments on a passage through the turn's token
// of the NAS, the thread reading Codex on <host>. The computer stopped, the
// strip says it is offline and the composer holds a message until it is
// back, when it runs.
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const PAGE = { width: 612, height: 792 };
const GRACE_S = 3;
const CHAT_REPLY = "Tightened the sentence in sections/method.tex:3.";
const BUILD = "latexmk -pdf main.tex";
// 4.2: this message's turn, on a library PDF of the NAS, comments on a passage with the NAS's tools
const LOOSE_ASK = "ask the authors about noon";
const NOON = "Marigolds open at noon";
const LOOSE_QUESTION = "Is noon right here?";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function remoteChatScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, makePdf, sleep } = env;
  if (!wanted("remote-chat")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-remote-chat-"));
  const agentLog = path.join(disk, "agent.jsonl");
  const nas = new Server({ env: { GAMMA_COMPUTER_GRACE_S: String(GRACE_S) } });
  const computer = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    // no Claude Code here: a path that is no file, so the computer finds none
    GAMMA_AGENT_CLAUDE: path.join(disk, "no-claude.exe"),
    // a chat turn reports one command and answers after a pause long enough
    // to be seen working, stopped, and found again from another tab; each new
    // session is a thread of its own id, so a resumed one shows
    FAKE_AGENT: JSON.stringify({ chat: CHAT_REPLY, commands: [BUILD], delay: 8, log: agentLog, distinct: true, turns: {
      [LOOSE_ASK]: { gamma: [{ tool: "comment", arguments: { page_id: "$page", quote: NOON, pdf_page: 1, body: LOOSE_QUESTION } }],
        commands: [], delay: 0, chat: "Commented:\n\n{gamma}" } } }),
  } });
  try {
    await nas.start();
    await computer.start();
    for (const [name, pw] of [["rc-alice", "rc-alice-pw"], ["rc-bob", "rc-bob-pw"]]) await nas.manage("create-user", name, pw);
    const out = await nas.manage("create-workspace", "Optics lab", "rc-alice", "shared");
    const wsId = (out.match(/workspace (\S+)/) || [])[1];
    assert(wsId, `shared workspace id from: ${out}`);
    await nas.manage("set-member", wsId, "rc-bob", "editor");
    const alice = await new Account(nas, "rc-alice", "rc-alice-pw").login();
    const bob = await new Account(nas, "rc-bob", "rc-bob-pw").login();
    alice.ws = bob.ws = wsId;
    // the computer's own account, which the desktop window is signed into
    await computer.manage("create-user", "rc-owner", "rc-owner-pw");
    const owner = await new Account(computer, "rc-owner", "rc-owner-pw").login();

    const folderId = await alice.folder("Paper");
    const filesOf = async (folder) => (await alice.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const heard = () => fs.readFileSync(agentLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
    // written as an editor that finished a while ago, so a round takes it at once
    const dir = path.join(disk, "Paper");
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      const file = path.join(dir, rel);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, fs.readFileSync(path.join(PAPER, "v1", rel)));
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    }

    let pdf = "";
    const at = () => `${nas.base}/?ws=${wsId}&page=${pdf}`;
    const computersOf = async (account) => (await account.api(`/api/pages/${pdf}/computer`)).computers;
    const chatOf = (account) => account.api(`/api/local-agents/chat?bucket=${pdf}`);
    const conversation = async () => (await chatOf(alice)).conversation;
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
    // already (no AI service: the agent is the chat's own), its strip shown.
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
      if (!(await dock.locator(".computerStrip").count())) {
        await dock.locator(".chatModelChip .uiSelectBtn").click();
        await page.getByRole("menuitemradio", { name: /^Codex on / }).click();
      }
      await dock.locator(".computerStrip").waitFor();
      return dock;
    };
    const stripText = async (dock) => (await dock.locator(".computerStrip").innerText().catch(() => "")).trim();
    // The strip's line alone, and where the agent works after it (a page in no synced folder).
    const stripLine = async (dock) => (await dock.locator(".computerStrip .chatHealthText").innerText().catch(() => "")).trim();
    const stripPlace = async (dock) => (await dock.locator(".computerStrip .computerPlace").innerText().catch(() => "")).trim();
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
    const lastState = async () => (await conversation())?.messages.at(-1)?.state;

    let name = "", folder = "";
    await step("remote-chat: the computer keeps the NAS's work folder with Codex on, and alice's plain browser on the NAS has the chat's Codex side, its strip naming the computer", async () => {
      const found = (await owner.api("/api/local-agents?fresh=1")).agents;
      const codex = found.find((a) => a.name === "codex");
      assert(codex?.found && codex.ready, `Codex is found and ready on the computer: ${JSON.stringify(codex)}`);
      // as the shell's makeRemoteWorkFolder: a write token on the NAS, the work folder on the host
      const token = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "Gamma desktop: Paper synced folder", scope: "write", expires_in_days: 365 } });
      const link = await owner.api("/api/folder-links", { method: "POST", body: { folder: folderId, path: dir, notes: true, pull: true,
        push: "everything", choose: true, remote_url: nas.base, workspace: wsId, token: token.token, token_id: token.id } });
      assertEq(JSON.stringify([link.work, link.choosing]), JSON.stringify([true, true]), "a work folder of the NAS, its files to be chosen");
      const saved = await owner.api(`/api/folder-links/${link.id}/selection`, { method: "PUT", body: { base_hash: "",
        selection: { base: "preset", changes: [{ path: "main.pdf", on: true }, { format: "latex", on: true }] } } });
      assertEq(saved.choosing, false, "its files are chosen");
      const main = await until(async () => {
        const now = await owner.api(`/api/folder-links/${link.id}`);
        if (now.status?.last_error) throw new Error(`the round failed: ${now.status.last_error}`);
        return (await filesOf(folderId)).find((p) => p.properties?.file_name === "main.pdf");
      }, { timeout: 45000, every: 500, what: "main.pdf pushed into the NAS's folder" });
      pdf = main.id;
      // as the shell's showComputer: a computer token minted on the NAS, handed to the host
      const minted = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: `Gamma desktop: ${os.hostname()}`, scope: "computer", expires_in_days: 365 } });
      const made = await owner.api("/api/computer-links", { method: "POST",
        body: { remote_url: nas.base, workspace: wsId, token: minted.token, token_id: minted.id } });
      await until(async () => (await owner.api("/api/computer-links")).links.find((l) => l.id === made.link.id)?.status?.state === "connected",
        { timeout: 45000, every: 500, what: "the channel connected" });
      const turned = await owner.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: true } });
      assertEq(turned.enabled, true, "Codex on for the computer");
      const [seen] = await until(async () => {
        const list = await computersOf(alice);
        return list.length && list[0].online && list[0].vault?.work
          && list[0].assistants?.some((a) => a.name === "codex" && a.enabled) ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer, online, keeping its work folder with Codex on" });
      name = seen.name;
      assertEq(name.toLowerCase(), os.hostname().slice(0, 64).toLowerCase(), "the computer's host name");

      // the NAS's chat route answers for the remote seat
      const chat = await until(async () => {
        const got = await chatOf(alice);
        return got.computer?.ready === true && got.computer.models?.length ? got : null;
      }, { timeout: 30000, every: 500, what: "the chat's computer, ready, with its models" });
      const c = chat.computer;
      assertEq(JSON.stringify([c.agent, c.label, c.remote, c.computer, c.online, c.since, c.paused, c.turn, c.session]),
        JSON.stringify(["codex", "Codex", true, name, true, null, false, null, false]), "the remote seat's strip facts");
      assert(c.models.some((m) => m.id === "gpt-fake-mini"), `the models the computer announced: ${JSON.stringify(c.models)}`);
      assertEq(chat.conversation ?? null, null, "no conversation yet");
      folder = c.folder || "";
      assert(!JSON.stringify(chat).includes(disk), "no path of the computer reaches the NAS");

      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
        assertEq((await agentsListed(page, dock)).join("|"), `Gamma AI|Codex on ${name}`,
          "the chip lists Gamma AI and the agent of the computer that keeps the folder");
        await codexSide(page);
        const ready = `Codex on ${name}${folder ? ` · ${folder}` : ""}`;
        await until(async () => (await stripText(dock)) === ready, { what: `the strip reads ${ready}: ${await stripText(dock)}` });
        await dock.locator(".chatEmpty", { hasText: `it works in the synced folder's directory on ${name}.` }).waitFor();
        assertEq(await dock.getByRole("textbox", { name: "Message Codex", exact: true }).getAttribute("placeholder"),
          `Message Codex on ${name}…`, "the composer names the computer");
        assertEq(await dock.locator(".computerStripMenu").count(), 0, "no menu: Show in folder and the terminal need the computer itself");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let chatThread = "";
    await step("remote-chat: alice's message with a PDF passage runs on the computer, a Background tasks row while it runs, and its reply and steps come back into the NAS's conversation", async () => {
      const before = heard().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await selectOnPdf(page, 1, "The penalty");
        const chip = dock.locator(".chatSelChips .chatSelChip").first();
        await chip.waitFor();
        await until(async () => (await chip.innerText()).includes("p. 1"), { what: `the chip names the page: ${await chip.innerText()}` });
        assert(!(await chip.innerText()).includes(".tex"), "and no source line: that needs the computer's disk");
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("tighten this sentence");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.user", { hasText: "tighten this sentence" }).waitFor();
        assertEq(await dock.locator(".chatSelChips").count(), 0, "the passage went with the message");
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).waitFor({ timeout: 10000 });
        await until(async () => (await stripText(dock)).includes("Codex is working"),
          { timeout: 20000, every: 250, what: `the strip says Codex is working: ${await stripText(dock)}` });
        // the turn the NAS holds for the account is a row of Background tasks
        await page.click("button[aria-label='Background tasks']");
        const row = page.locator(".tasksPopover .taskRow", { hasText: `Codex on ${name}` });
        await row.waitFor({ timeout: 10000 });
        await page.keyboard.press("Escape");
        await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor({ timeout: 60000 });
        await dock.getByRole("button", { name: "Send", exact: true }).waitFor();
        await until(async () => (await dock.locator(".chatSteps").last().innerText()).trim() === "1 step",
          { what: `the steps pill: ${await dock.locator(".chatSteps").last().innerText().catch(() => "")}` });
        const ready = `Codex on ${name}${folder ? ` · ${folder}` : ""}`;
        await until(async () => (await stripText(dock)) === ready, { what: `the strip ready again: ${await stripText(dock)}` });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const stored = await conversation();
      assertEq(JSON.stringify(stored.messages.map((m) => [m.role, m.role === "agent" ? m.state : m.text])),
        JSON.stringify([["user", "tighten this sentence"], ["agent", "done"]]), "the conversation as the NAS keeps it");
      const reply = stored.messages[1];
      assertEq(reply.text, CHAT_REPLY, "Codex's reply");
      assertEq(JSON.stringify(reply.steps.map((s) => [s.kind, s.program])), JSON.stringify([["run", "latexmk"]]), "its step, the command's program");
      assertEq(stored.messages[0].selections[0].page, 1, "the passage's page");
      const since = heard().slice(before);
      const servers = since.filter((e) => e.argv?.[0] === "app-server");
      assert(servers.some((e) => path.relative(e.cwd, dir) === ""), `an app-server ran in the paper's directory: ${JSON.stringify(servers)}`);
      const turns = since.filter((e) => e.in?.method === "turn/start");
      assertEq(turns.length, 1, "one turn");
      chatThread = turns[0].in.params.threadId;
      const prompt = turns[0].in.params.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(prompt.includes('"message": "tighten this sentence"') && prompt.includes("The penalty") && prompt.includes("main.pdf"),
        `the message, the passage and the file reach Codex: ${prompt.slice(0, 800)}`);
      assert(prompt.includes("sections/method.tex"), `the passage's source line, computed on the computer: ${prompt.slice(0, 1200)}`);
    });

    await step("remote-chat: a tab closed mid-turn finds the turn and its reply in a new one, and the follow-up resumes Codex's session on the computer", async () => {
      const before = heard().length;
      let ctx = await alice.context(browser);
      let page = await openPage(ctx, at());
      await waitForPdf(page, 1);
      let dock = await codexSide(page);
      await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).waitFor();
      await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("and the next one too");
      await dock.getByRole("button", { name: "Send", exact: true }).click();
      await dock.getByRole("button", { name: "Stop Codex", exact: true }).waitFor({ timeout: 10000 });
      await ctx.close(); // the tab goes; the turn does not
      await until(async () => (await lastState()) === "running", { timeout: 20000, every: 250, what: "the turn runs on, on the computer" });
      ctx = await alice.context(browser);
      page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        dock = await codexSide(page);
        await dock.locator(".chatBubble.user", { hasText: "and the next one too" }).waitFor();
        await dock.locator(".chatSteps.live").waitFor({ timeout: 10000 });
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === 2,
          { timeout: 60000, what: "the second reply, seen in the new tab" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      assertEq(JSON.stringify(since.filter((e) => e.in?.method === "thread/resume").map((e) => e.in.params.threadId)),
        JSON.stringify([chatThread]), "the conversation's session resumed");
      assertEq(since.filter((e) => e.in?.method === "thread/start").length, 0, "no new session");
      assertEq((await conversation()).messages.at(-1).fresh, false, "nothing forgotten");
    });

    await step("remote-chat: Stop of a running turn ends it as stopped on the computer, and New chat keeps the conversation in History", async () => {
      const before = heard().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("rewrite the whole method");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        // running on the computer, not merely queued on the NAS: claimed there and Codex's turn started
        await until(async () => (await lastState()) === "running", { timeout: 20000, every: 250, what: "the turn claimed by the computer" });
        await until(async () => heard().slice(before).some((e) => e.in?.method === "turn/start"),
          { timeout: 20000, every: 250, what: "Codex's turn started on the computer" });
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).click({ timeout: 10000 });
        await dock.locator(".chatBubble.ai", { hasText: "(stopped)" }).waitFor({ timeout: 30000 });
        await dock.getByRole("button", { name: "Send", exact: true }).waitFor();
        assertEq(await lastState(), "stopped", "stored as stopped");
        await dock.getByRole("button", { name: "New chat", exact: true }).click();
        await dock.locator(".chatEmpty", { hasText: "Ask Codex about this file" }).waitFor();
        await dock.getByRole("button", { name: "Chat history", exact: true }).click();
        await page.locator(".chatHistoryPop .chatHistRow", { hasText: "tighten this sentence" }).click();
        await dock.locator(".chatBubble.user", { hasText: "rewrite the whole method" }).waitFor();
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assert(heard().slice(before).some((e) => e.in?.method === "turn/interrupt"), "Codex was interrupted on the computer");
      assertEq((await alice.api(`/api/local-agents/chat/history?bucket=${pdf}`)).conversations.length, 0,
        "the opened conversation is the active one again");
    });

    // Step 3.5 of docs/research/agents-as-participants-plan.md, over the channel.
    await step("remote-chat: in alice's reply on the NAS the @ menu lists Codex on the computer, @codex ticks Send to Codex, and the reply is a turn of its thread run there, its region's picture in the prompt", async () => {
      const area = await alice.api("/api/blocks", { method: "POST", body: { parent_id: pdf, content: "",
        properties: { pdf_position: areaOf(PLACES.v1.figure.rect), color: "rgba(255, 226, 143, 0.65)" } } });
      const caption = (await alice.api(`/api/pages/${pdf}/comments`, { method: "POST",
        body: { block_id: area.id, body: "the caption says 2025" } })).thread;
      const threadsOf = async () => (await alice.api(`/api/pages/${pdf}/comments?state=all`)).threads;
      const before = heard().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        if (!(await page.locator(".commentsPanel").count())) {
          await page.getByRole("button", { name: "View", exact: true }).click();
          await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
          await page.keyboard.press("Escape");
        }
        const panelRow = page.locator(`.commentsPanelRow[data-thread-id="${caption.id}"]`);
        await panelRow.waitFor({ timeout: 10000 });
        await panelRow.click();
        const thread = page.locator(".commentThread").first();
        await thread.locator(".commentBody").first().click();
        const field = thread.locator(".commentField .cm-content").last();
        await field.waitFor();
        await field.click();
        await page.keyboard.type("@cod");
        const menu = page.locator(".mentionMenu");
        const row = menu.locator(".refPopupItem.selected");
        await until(async () => (await row.locator(".refPopupText").innerText().catch(() => "")).startsWith("Codex"),
          { what: "the @ menu offers the folder's agent" });
        assertEq((await row.locator(".mentionAside").innerText()).trim(), `on ${name}`, "where it runs");
        await page.keyboard.press("Enter");
        await menu.waitFor({ state: "detached" });
        await thread.locator(".cmMentionChip", { hasText: "@codex" }).waitFor();
        const tick = thread.locator(".commentSendTo input[type=checkbox]");
        await until(() => tick.isChecked(), { what: "@codex ticks the box" });
        assertEq((await thread.locator(".commentSendTick").innerText()).trim(), "Send to Codex", "the tick names the agent");
        await page.keyboard.type("fix this only");
        await page.keyboard.press("Shift+Enter");
        // the computer takes the turn over its channel; its end is the agent's reply in the thread there
        const answer = thread.locator(".commentItem.reply", { hasText: CHAT_REPLY });
        await answer.waitFor({ timeout: 60000 });
        assertEq((await answer.locator(".commentAuthor").textContent()).trim(), "Codex", "the reply is Codex's");
        // the reply's own @codex is the agent's chip, titled with the computer
        const chip = thread.locator(".commentItem.reply .mentionChip.mention-agent", { hasText: "@codex" }).first();
        await chip.waitFor();
        assertEq(await chip.getAttribute("title"), `Agent: Codex on ${name}`, "the chip names the computer");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const reply = (await threadsOf()).find((th) => th.id === caption.id).comments.at(-1);
      assertEq(JSON.stringify([reply.agent, reply.agent_kind, reply.author, reply.agent_on]),
        JSON.stringify(["Codex", "computer", "rc-alice", name]), "signed as the computer's agent there, for alice");
      const turn = heard().slice(before).filter((e) => e.in?.method === "turn/start").at(-1).in.params;
      const told = turn.input.map((p) => p.text || "").join("\n");
      assert(told.includes('"message": "fix this only"') && told.includes(`"thread_id": "${caption.id}"`),
        `the comment and its thread reach Codex on the computer: ${told.slice(0, 600)}`);
      const pictures = turn.input.filter((p) => p.type === "localImage").map((p) => p.path);
      assert(pictures.length && pictures.every((p) => p.startsWith(path.join(dir, ".gamma", "annotations", "img"))),
        `the region's picture, fetched into the folder on the computer: ${JSON.stringify(pictures)}`);
    });

    await step("remote-chat: bob, another member, has no Codex side on the page, and the chat routes refuse him and a token", async () => {
      assertEq(JSON.stringify(await computersOf(bob)), "[]", "the page's computers for another member");
      const ctx = await bob.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await sleep(800);
        const listed = (await dock.locator(".chatModelChip").count()) ? await agentsListed(page, dock) : [];
        assertEq(listed.filter((n) => n.startsWith("Codex")).length, 0, "no Codex to pick");
        assertEq(await dock.locator(".computerStrip").count(), 0, "no computer side");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      // the expected refusals, outside the page
      const asBob = await fetch(`${nas.base}/api/local-agents/chat?bucket=${pdf}`, { headers: bob.headers() });
      assertEq(asBob.status, 404, `another member reads no seat: ${await asBob.text()}`);
      const sent = await fetch(`${nas.base}/api/local-agents/chat/turns`, { method: "POST",
        headers: bob.headers({ "Content-Type": "application/json" }), body: JSON.stringify({ bucket: pdf, text: "hello" }) });
      assertEq(sent.status, 404, `nor sends: ${await sent.text()}`);
      const token = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "a script", scope: "write", expires_in_days: 1 } });
      const asToken = await fetch(`${nas.base}/api/local-agents/chat?bucket=${pdf}`,
        { headers: { Authorization: `Bearer ${token.token}`, "X-Gamma-Workspace": wsId } });
      assertEq(asToken.status, 403, `a token is refused: ${await asToken.text()}`);
      await alice.api(`/api/integrations/tokens/${token.id}`, { method: "DELETE" });
      assertEq((await conversation()).messages.filter((m) => m.role === "user" && m.text === "hello").length, 0, "nothing written");
    });

    await step("remote-chat: the computer's owner lets bob use Codex there; bob's @codex in a comment on the paper is a turn on the computer, answered in his thread as Codex on the computer, for rc-bob", async () => {
      // whom the owner may name: the editors of the NAS's workspace, asked over the channel, the channel's own account left out
      const { places } = await owner.api("/api/local-agents/people");
      const there = places.find((p) => p.server === nas.base && p.workspace_id === wsId);
      const person = there?.people.find((p) => p.username === "rc-bob");
      assert(person && !there.people.some((p) => p.username === "rc-alice"), `bob is offered, alice is not: ${JSON.stringify(places)}`);
      const named = await owner.api("/api/local-agents/codex/people", { method: "POST",
        body: { server: nas.base, workspace_id: wsId, account: person.id } });
      assertEq(JSON.stringify(named.people.map((p) => p.username)), JSON.stringify(["rc-bob"]), "bob may use Codex");
      // announced: the NAS now shows bob the computer that keeps the page, that agent alone, lent by alice
      const [lent] = await until(async () => {
        const list = await computersOf(bob);
        return list.length && list[0].vault?.work ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer for bob" });
      assertEq(JSON.stringify([lent.name, lent.lent_by, lent.assistants.map((a) => [a.name, a.enabled, a.runs])]),
        JSON.stringify([name, "rc-alice", [["codex", true, "never"]]]), "the computer bob may use, lent by alice, never asking");
      const area = await bob.api("/api/blocks", { method: "POST", body: { parent_id: pdf, content: "",
        properties: { pdf_position: areaOf(PLACES.v1.figure.rect), color: "rgba(255, 226, 143, 0.65)" } } });
      const root = (await bob.api(`/api/pages/${pdf}/comments`, { method: "POST",
        body: { block_id: area.id, body: "the legend hides the curve" } })).thread;
      const before = heard().length;
      const ctx = await bob.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        if (!(await page.locator(".commentsPanel").count())) {
          await page.getByRole("button", { name: "View", exact: true }).click();
          await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
          await page.keyboard.press("Escape");
        }
        const row = page.locator(`.commentsPanelRow[data-thread-id="${root.id}"]`);
        await row.waitFor({ timeout: 10000 });
        await row.click();
        const thread = page.locator(".commentThread").first();
        await thread.locator(".commentBody").first().click();
        const field = thread.locator(".commentField .cm-content").last();
        await field.waitFor();
        await field.click();
        const tick = thread.locator(".commentSendTo input[type=checkbox]");
        assertEq(await tick.isChecked(), false, "a thread among people: the tick off");
        await page.keyboard.type("@cod");
        const menu = page.locator(".mentionMenu");
        await until(async () => (await menu.locator(".refPopupItem.selected .refPopupText").innerText().catch(() => "")).startsWith("Codex"),
          { what: "the @ menu offers bob the agent lent to him" });
        await page.keyboard.press("Enter");
        await menu.waitFor({ state: "detached" });
        await until(() => tick.isChecked(), { what: "@codex ticks the box" });
        await page.keyboard.type("move it inside the plot");
        await page.keyboard.press("Shift+Enter");
        const answer = thread.locator(".commentItem.reply", { hasText: CHAT_REPLY });
        await answer.waitFor({ timeout: 60000 });
        assertEq((await answer.locator(".commentAuthor").textContent()).trim(), "Codex", "the reply is Codex's");
        assertEq(await answer.locator(".commentFor").textContent(), `on ${name}, for rc-bob`, "on its computer, for bob");
        assertEq(await answer.locator(".commentAuthor").getAttribute("title"), `Written by rc-alice's Codex on ${name}, for rc-bob`,
          "the byline's title names its owner too");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const turns = heard().slice(before).filter((e) => e.in?.method === "turn/start");
      assertEq(turns.length, 1, "one turn, on the computer");
      const told = turns[0].in.params.input.map((p) => p.text || "").join("\n");
      assert(told.includes('"message": "move it inside the plot"') && told.includes("rc-bob"), `bob's words, as his: ${told.slice(0, 600)}`);
      const stored = (await bob.api(`/api/pages/${pdf}/comments`)).threads.find((th) => th.id === root.id);
      const theirs = stored.comments.at(-1);
      assertEq(JSON.stringify([theirs.agent, theirs.author, theirs.agent_on, theirs.agent_owner]),
        JSON.stringify(["Codex", "rc-bob", name, "rc-alice"]), "Codex answered for bob, alice its owner");
      // the conversation is bob's, read by alice too, and by no one else
      const his = (await bob.api(`/api/local-agents/chat?bucket=${pdf}&thread=${root.id}`)).conversation;
      const hers = (await alice.api(`/api/local-agents/chat?bucket=${pdf}&thread=${root.id}`)).conversation;
      assertEq(JSON.stringify([his?.id, his?.thread_id]), JSON.stringify([hers?.id, root.id]), "bob's conversation of his thread, which alice reads");
    });

    // Step 4.2 of docs/research/agents-as-participants-plan.md: any chat there.
    await step("remote-chat: on a library PDF of the NAS in no synced folder alice picks Codex on the computer, whose turn runs there in a directory of its own and comments on a passage through the turn's token, as Codex on the computer", async () => {
      const up = await alice.upload("/api/uploads", makePdf([[NOON, "and close at dusk"]]), "marigold.pdf", "application/pdf");
      const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Marigold paper", source_url: up.source_url, original_filename: "marigold.pdf" } });
      const [seen] = (await alice.api(`/api/pages/${paper.id}/computer`)).computers;
      assertEq(JSON.stringify([seen?.name, seen?.vault ?? null]), JSON.stringify([name, null]),
        "the computer, its agents the page's though it keeps none of it");
      const before = heard().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, `${nas.base}/?ws=${wsId}&page=${paper.id}`);
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await until(async () => (await stripLine(dock)) === `Codex on ${name}`, { what: `the strip names the computer: ${await stripText(dock)}` });
        assertEq(await stripPlace(dock), "No folder", "the computer offers no directory: one of its own");
        await dock.locator(".chatEmpty", { hasText: `it works in an empty directory of its own on ${name}.` }).waitFor();
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(LOOSE_ASK);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.ai", { hasText: "Commented:" }).waitFor({ timeout: 60000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const since = heard().slice(before);
      const called = since.find((e) => e.gamma === "comment");
      assert(called && called.http === 200 && !called.error && called.text.includes(NOON),
        `the call commented with the NAS's tools and the turn's token: ${JSON.stringify(called)}`);
      const servers = since.filter((e) => e.argv?.[0] === "app-server");
      // (a probe of the catalog's may start one of its own elsewhere, never in the paper's)
      assert(servers.some((e) => e.cwd.includes(`${path.sep}agents${path.sep}`))
        && servers.every((e) => path.relative(dir, e.cwd).startsWith("..")),
        `Codex ran in a directory of its own on the computer, not the paper's: ${JSON.stringify(servers.map((e) => e.cwd))}`);
      const thread = (await alice.api(`/api/pages/${paper.id}/comments`)).threads.find((th) => th.comments[0]?.body === LOOSE_QUESTION);
      assert(thread, "the agent's thread is on the NAS's page");
      const [said] = thread.comments;
      assertEq(JSON.stringify([said.author, said.agent, said.agent_kind, said.agent_on]),
        JSON.stringify(["rc-alice", "Codex", "computer", name]), "written for alice, as Codex on the computer");
      const stored = (await alice.api(`/api/local-agents/chat?bucket=${paper.id}`)).conversation;
      assertEq(stored?.messages.at(-1).state, "done", "the conversation the NAS keeps");
      assert(!JSON.stringify(stored).includes(disk), "no path of the computer reaches the NAS");
    });

    await step("remote-chat: the computer stopped, the strip says it is offline and the composer holds a message until it is back, when it runs", async () => {
      const codexId = `computer:${(await computersOf(alice))[0].id}:codex`;
      await computer.halt();
      // its held poll ends (at most the 25 s hold), then the grace
      await until(async () => (await computersOf(alice))[0]?.online === false,
        { timeout: 25000 + GRACE_S * 1000 + 15000, every: 500, what: "the NAS reads the computer offline" });
      const away = await chatOf(alice);
      assertEq(JSON.stringify([away.computer?.online, typeof away.computer?.since, away.computer?.problem]),
        JSON.stringify([false, "string", ""]), "the seat offline, since when, no problem told");
      const refused = await fetch(`${nas.base}/api/local-agents/chat/turns`, { method: "POST",
        headers: alice.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ bucket: pdf, text: "are you there", agent: codexId }) });
      assertEq(refused.status, 409, `no turn is queued for a computer away: ${await refused.text()}`);
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await until(async () => (await stripText(dock)).startsWith(`${name} is offline`)
          && (await stripText(dock)).endsWith("send this once it connects."),
        { what: `the strip says the computer is offline: ${await stripText(dock)}` });
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("are you there");
        assertEq(await dock.getByRole("button", { name: "Send", exact: true }).isDisabled(), true, "the composer holds the message");

        // the computer back, on the same data: its channel announces, and the message goes and runs
        const replies = await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count();
        await computer.start();
        const ready = `Codex on ${name}${folder ? ` · ${folder}` : ""}`;
        await until(async () => (await stripText(dock)) === ready, { timeout: 60000, what: `the strip ready again: ${await stripText(dock)}` });
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.user", { hasText: "are you there" }).waitFor();
        await until(async () => {
          const state = await lastState();
          if (state === "failed" || state === "stopped") throw new Error(`the turn ended ${state}`);
          return state === "done";
        }, { timeout: 120000, every: 1000, what: "the turn done" });
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === replies + 1,
          { timeout: 30000, what: "its reply in the open tab" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assertEq((await computersOf(alice))[0]?.online, true, "the computer reads online again");
    });
  } finally {
    await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}

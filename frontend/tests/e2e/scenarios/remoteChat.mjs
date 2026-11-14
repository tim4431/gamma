import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// The chat's computer side on another server's page (step 4d of
// docs/research/agent-collab.md; docs/dev/local_agents.md "A work folder of
// another server"): the two Gammas of remoteSends.mjs, a NAS (a plain
// server, its grace before a computer reads offline cut to 3 s) and a
// computer (the desktop app's own server with local agents on and the
// stand-in Codex of backend/tests/fake_agents/). On the NAS a shared
// workspace holds the folder "Paper", its maker alice and an editor, bob; the
// computer's own account makes a work folder of it from the SyncTeX sample
// paper's directory, shows the computer there and turns Codex on for it.
// Alice's plain browser on the NAS then has the Gamma AI | Codex switch in the
// PDF page's chat, its strip naming Codex on the computer: a message with a
// PDF passage runs on the computer and its reply and steps come back into the
// conversation the NAS keeps; a tab closed mid-turn finds the turn again and
// the follow-up resumes Codex's session there; Stop of a running turn ends
// it; New chat and History work as locally. Bob has no Codex side. The
// computer stopped, the strip says it is offline, a message waits for it
// ("Waiting for <host>") and runs once it is back.
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const GRACE_S = 3;
const CHAT_REPLY = "Tightened the sentence in sections/method.tex:3.";
const BUILD = "latexmk -pdf main.tex";

export async function remoteChatScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep } = env;
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
    FAKE_AGENT: JSON.stringify({ chat: CHAT_REPLY, commands: [BUILD], delay: 8, log: agentLog, distinct: true }),
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
    const chatOf = (account) => account.api(`/api/local-agents/chat?page=${pdf}`);
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
    // The chat's Codex side, its strip shown.
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".segGroup").getByRole("button", { name: "Codex", exact: true }).click();
      await dock.locator(".computerStrip").waitFor();
      return dock;
    };
    const stripText = async (dock) => (await dock.locator(".computerStrip").innerText().catch(() => "")).trim();
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
        body: { name: "Gamma desktop: Paper synced vault", scope: "write", expires_in_days: 365 } });
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
      const turned = await owner.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "codex" } });
      assertEq(turned.agent?.name, "codex", "Codex on for the work folder");
      const [seen] = await until(async () => {
        const list = await computersOf(alice);
        return list.length && list[0].online && list[0].vault?.agent?.name === "codex" ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer, online, its vault with Codex" });
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
        // Their names: a narrow window shows the sides' icons alone.
        assertEq(await dock.locator(".segGroup button").evaluateAll((b) => b.map((x) => x.getAttribute("aria-label")).join("|")), "Gamma AI|Codex",
          "the switch names Gamma AI and the folder's agent");
        await codexSide(page);
        const ready = `Codex on ${name}${folder ? ` · ${folder}` : ""}`;
        await until(async () => (await stripText(dock)) === ready, { what: `the strip reads ${ready}: ${await stripText(dock)}` });
        await dock.locator(".chatEmpty", { hasText: `it works in the work folder's directory on ${name}.` }).waitFor();
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
      assert(!turns[0].in.params.outputSchema, "a chat turn answers in prose");
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
        await dock.locator(".computerEnded", { hasText: "Stopped" }).waitFor({ timeout: 30000 });
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
      assertEq((await alice.api(`/api/local-agents/chat/history?page=${pdf}`)).conversations.length, 0,
        "the opened conversation is the active one again");
    });

    await step("remote-chat: bob, another member, has no Codex side on the page, and the chat routes refuse him and a token", async () => {
      assertEq(JSON.stringify(await computersOf(bob)), "[]", "the page's computers for another member");
      const ctx = await bob.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await openChat(page);
        await sleep(800);
        assertEq(await dock.getByRole("button", { name: "Codex", exact: true }).count(), 0, "no switch to Codex");
        assertEq(await dock.locator(".computerStrip").count(), 0, "no computer side");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      // the expected refusals, outside the page
      const asBob = await fetch(`${nas.base}/api/local-agents/chat?page=${pdf}`, { headers: bob.headers() });
      assertEq(asBob.status, 404, `another member reads no seat: ${await asBob.text()}`);
      const sent = await fetch(`${nas.base}/api/local-agents/chat/turns`, { method: "POST",
        headers: bob.headers({ "Content-Type": "application/json" }), body: JSON.stringify({ page: pdf, text: "hello" }) });
      assertEq(sent.status, 404, `nor sends: ${await sent.text()}`);
      const token = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "a script", scope: "write", expires_in_days: 1 } });
      const asToken = await fetch(`${nas.base}/api/local-agents/chat?page=${pdf}`,
        { headers: { Authorization: `Bearer ${token.token}`, "X-Gamma-Workspace": wsId } });
      assertEq(asToken.status, 403, `a token is refused: ${await asToken.text()}`);
      await alice.api(`/api/integrations/tokens/${token.id}`, { method: "DELETE" });
      assertEq((await conversation()).messages.filter((m) => m.role === "user" && m.text === "hello").length, 0, "nothing written");
    });

    await step("remote-chat: the computer stopped, the strip says it is offline and a message waits for it, and runs once the computer is back", async () => {
      await computer.halt();
      // its held poll ends (at most the 25 s hold), then the grace
      await until(async () => (await computersOf(alice))[0]?.online === false,
        { timeout: 25000 + GRACE_S * 1000 + 15000, every: 500, what: "the NAS reads the computer offline" });
      const away = await chatOf(alice);
      assertEq(JSON.stringify([away.computer?.online, typeof away.computer?.since, away.computer?.problem]),
        JSON.stringify([false, "string", ""]), "the seat offline, since when, no problem told");
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await until(async () => (await stripText(dock)).startsWith(`${name} is offline`)
          && (await stripText(dock)).endsWith("a message waits and runs when it connects"),
        { what: `the strip says the computer is offline: ${await stripText(dock)}` });
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill("are you there");
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        await dock.locator(".chatBubble.user", { hasText: "are you there" }).waitFor();
        const pill = dock.locator(".chatSteps").last();
        await until(async () => (await pill.innerText().catch(() => "")).trim() === `Waiting for ${name}`,
          { what: `the turn's pill reads Waiting for ${name}: ${await pill.innerText().catch(() => "")}` });
        await until(async () => (await stripText(dock)).startsWith(`Waiting for ${name}`),
          { what: `the strip says the message waits: ${await stripText(dock)}` });
        assertEq(await lastState(), "queued", "the turn waits, queued on the NAS");
        await sleep(1500);
        assertEq(await lastState(), "queued", "and keeps waiting while the computer is away");

        // the computer back, on the same data: its channel announces, asks for the chat turns that wait and runs this one
        const replies = await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count();
        await computer.start();
        await until(async () => {
          const state = await lastState();
          if (state === "failed" || state === "stopped") throw new Error(`the waiting turn ended ${state}`);
          return state === "done";
        }, { timeout: 120000, every: 1000, what: "the waiting turn done" });
        await until(async () => (await dock.locator(".chatBubble.ai", { hasText: CHAT_REPLY }).count()) === replies + 1,
          { timeout: 30000, what: "its reply in the open tab" });
        const ready = `Codex on ${name}${folder ? ` · ${folder}` : ""}`;
        await until(async () => (await stripText(dock)) === ready, { timeout: 20000, what: `the strip ready again: ${await stripText(dock)}` });
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

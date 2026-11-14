import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { marginLeft } from "../../../src/comments/marginLayout.js";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { dragSelectPdfText, waitForPdf } from "./pdf.mjs";

// Sends and mentions of another server's work folder run on the computer
// (step 4c of docs/research/agent-collab.md; docs/dev/computers.md,
// docs/dev/local_agents.md): two Gammas, a NAS (a plain server, its grace
// before a computer reads offline cut to 3 s) and a computer (the desktop
// app's own server: the vaults recipe of GAMMA_FOLDER_LINKS with no shell
// secret, local agents on, the stand-in Codex of backend/tests/fake_agents/
// found and Claude Code not). On the NAS a shared workspace holds an empty
// folder "Paper", its maker alice and an editor, bob; on the computer its own
// account makes a work folder of that folder from a directory holding the
// SyncTeX sample paper (as remoteWork.mjs: a write token minted there, its
// files chosen and pushed), shows the computer there (a computer token, as
// computers.mjs) and turns Codex on for it. Alice's plain browser on the NAS
// then comments on a passage of the PDF and sends it from the Feedback dialog,
// which names Codex on the computer: the stand-in answers on the computer and
// the NAS has its reply signed Codex, the thread resolved, the request done.
// An @codex mention in a reply runs the same way; bob sees no agent and his
// @codex sends nothing, and a token's Send is refused. The computer stopped,
// alice's Send waits ("Waiting for <host>"), and runs once it is back.
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAGE = { width: 612, height: 792 };
const GRACE_S = 3;
const SUMMARY = "Introduced the penalty's weight.";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function remoteSendScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep } = env;
  if (!wanted("remote-sends")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-remote-sends-"));
  const agentLog = path.join(disk, "agent.jsonl");
  const nas = new Server({ env: { GAMMA_COMPUTER_GRACE_S: String(GRACE_S) } });
  const computer = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    // no Claude Code here: a path that is no file, so the computer finds none
    GAMMA_AGENT_CLAUDE: path.join(disk, "no-claude.exe"),
    // Codex answers every comment it is handed as fixed, after a pause long enough to be seen working
    FAKE_AGENT: JSON.stringify({ each: "fixed", summary: SUMMARY, delay: 2, log: agentLog, distinct: true }),
  } });
  try {
    await nas.start();
    await computer.start();
    for (const [name, pw] of [["rs-alice", "rs-alice-pw"], ["rs-bob", "rs-bob-pw"]]) await nas.manage("create-user", name, pw);
    const out = await nas.manage("create-workspace", "Optics lab", "rs-alice", "shared");
    const wsId = (out.match(/workspace (\S+)/) || [])[1];
    assert(wsId, `shared workspace id from: ${out}`);
    await nas.manage("set-member", wsId, "rs-bob", "editor");
    const alice = await new Account(nas, "rs-alice", "rs-alice-pw").login();
    const bob = await new Account(nas, "rs-bob", "rs-bob-pw").login();
    alice.ws = bob.ws = wsId;
    // the computer's own account, which the desktop window is signed into
    await computer.manage("create-user", "rs-owner", "rs-owner-pw");
    const owner = await new Account(computer, "rs-owner", "rs-owner-pw").login();

    const folderId = await alice.folder("Paper");
    const filesOf = async (folder) => (await alice.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const threadsOf = async (page) => (await alice.api(`/api/pages/${page}/comments?state=all`)).threads;
    const requestsOf = async (account) => (await account.api("/api/feedback/requests?after=0")).requests;
    const doneRequest = (id, timeout = 60000) => until(async () => {
      const found = (await requestsOf(alice)).find((r) => r.id === id);
      if (found?.state === "failed") throw new Error(`request ${id} failed: ${found.detail}`);
      return found?.state === "done" ? found : null;
    }, { timeout, every: 500, what: `request ${id} done` });
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

    // The PDF's margin, zoomed out until the column has room beside the page (feedback.mjs).
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
      await d.locator(".feedbackEntry").first().waitFor({ timeout: 10000 });
      return d;
    };
    const notes = async (d) => (await d.locator(".feedbackAgentNote").allInnerTexts()).map((s) => s.trim());
    const openThread = async (page, threadId) => {
      const panel = await openComments(page);
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
    let pdf = "";
    const at = () => `${nas.base}/?ws=${wsId}&page=${pdf}`;
    // A new thread on an area of page 1 of the PDF, by `account` through the API (no mention).
    const commentOn = async (account, place, body) => {
      const area = await account.api("/api/blocks", { method: "POST", body: { parent_id: pdf, content: "",
        properties: { pdf_position: areaOf(place), color: "rgba(255, 226, 143, 0.65)" } } });
      const made = await account.api(`/api/pages/${pdf}/comments`, { method: "POST", body: { block_id: area.id, body } });
      assert(!("send" in made), "a comment that names no agent sends nothing");
      return made.thread;
    };
    const computersOf = async (account) => (await account.api(`/api/pages/${pdf}/computer`)).computers;
    // What Codex answered in a thread on the NAS: its reply signed Codex, the thread resolved.
    const answeredThere = async (threadId) => {
      const got = (await threadsOf(pdf)).find((th) => th.id === threadId);
      assert(got?.resolved_at, `${threadId} is resolved on the NAS`);
      const reply = got.comments.at(-1);
      assertEq(JSON.stringify([reply?.agent, reply?.agent_kind, reply?.author, reply?.body]),
        JSON.stringify(["Codex", "local", "rs-alice", `Done for ${threadId}.`]), "Codex's reply, signed as an agent on the maker's computer");
    };

    let link = null, name = "";
    await step("remote-sends: the computer makes a work folder of the NAS's empty folder from the paper's directory, shows itself there, and turns Codex on for it", async () => {
      const found = (await owner.api("/api/local-agents?fresh=1")).agents;
      const codex = found.find((a) => a.name === "codex");
      assert(codex?.found && codex.ready, `Codex is found and ready on the computer: ${JSON.stringify(codex)}`);
      // as the shell's makeRemoteWorkFolder: a write token on the NAS, the work folder on the host
      const token = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "Gamma desktop: Paper synced vault", scope: "write", expires_in_days: 365 } });
      link = await owner.api("/api/folder-links", { method: "POST", body: { folder: folderId, path: dir, notes: true, pull: true,
        push: "everything", choose: true, remote_url: nas.base, workspace: wsId, token: token.token, token_id: token.id } });
      assertEq(JSON.stringify([link.work, link.choosing, link.remote_url]), JSON.stringify([true, true, nas.base]),
        "a work folder of the NAS, waiting for its files to be chosen");
      // the tracking dialog's Save: the preset (the PDFs), the paper's PDF and its LaTeX sources
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
      // the sync panel's agent row: Codex on, which nothing waits on yet
      const turned = await owner.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "codex" } });
      assertEq(JSON.stringify([turned.queued, turned.agent?.name]), JSON.stringify([0, "codex"]), "Codex on, no Send waiting");
      // the next announce names the folder's agent, and the NAS tells the maker
      const [seen] = await until(async () => {
        const list = await computersOf(alice);
        return list.length && list[0].online && list[0].vault?.agent?.name === "codex" ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer, online, its vault with Codex" });
      name = seen.name;
      assertEq(name.toLowerCase(), os.hostname().slice(0, 64).toLowerCase(), "the computer's host name");
      assertEq(JSON.stringify(seen.vault.agent), JSON.stringify({ name: "codex", label: "Codex" }), "the vault's agent");
      const doc = await alice.api(`/api/feedback?scope=${folderId}`);
      assertEq(JSON.stringify([doc.agent?.name, doc.agent?.computer, doc.agent?.online, doc.agent?.takes]),
        JSON.stringify(["codex", name, true, true]), "the feedback document names Codex on the computer for its maker");
      assert(!JSON.stringify(seen).includes(disk) && !JSON.stringify(doc).includes(disk), "no path of the computer reaches the NAS");
    });

    let passage = null;
    await step("remote-sends: alice's plain browser on the NAS comments on a passage and sends it from the Feedback dialog, which names Codex on the computer; Codex answers there", async () => {
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const margin = await openMargin(page);
        const tip = page.locator(".plainTip");
        await dragSelectPdfText(page, 1, "penalty keeps");
        await tip.waitFor();
        await tip.getByRole("button", { name: "Comment on this passage" }).click();
        const fresh = margin.locator(".commentCard.active .commentNew");
        await fresh.waitFor();
        await fresh.locator(".commentField .cm-content").click();
        await page.keyboard.type("lambda is never introduced");
        await page.keyboard.press("Shift+Enter");
        passage = await until(async () => (await threadsOf(pdf)).find((th) => th.comments[0]?.body === "lambda is never introduced"),
          { what: "the passage's thread is stored" });
        await page.keyboard.press("Escape");

        const d = await openFeedback(page);
        const send = d.getByRole("button", { name: "Send 1 comment to Codex", exact: true });
        await send.waitFor({ timeout: 10000 });
        await until(async () => (await notes(d)).includes(`Codex works on ${name}, in this folder's directory there, and answers each comment.`),
          { what: `the note names Codex on ${name}: ${JSON.stringify(await notes(d))}` });
        await send.click();
        const row = d.locator(".feedbackRequest");
        await row.waitFor({ timeout: 10000 });
        const state = row.locator(".feedbackRequestState");
        await until(async () => (await state.innerText()).trim() === "Done by Codex",
          { timeout: 60000, every: 250, what: `the request reads Done by Codex: ${await state.innerText().catch(() => "")}` });
        const detail = (await row.locator(".feedbackRequestDetail").innerText()).trim();
        assert(detail.includes(SUMMARY), `the request's detail has Codex's summary: ${detail}`);
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const [request] = await requestsOf(alice);
      assertEq(JSON.stringify([request?.state, request?.state_by, request?.threads.map((t) => t.id)]),
        JSON.stringify(["done", "Codex", [passage.id]]), "the request is done by Codex");
      await answeredThere(passage.id);
      // Codex ran on the computer, in the paper's directory, told of the thread and the passage's source line found there
      const servers = heard().filter((e) => e.argv?.[0] === "app-server");
      assert(servers.some((e) => path.relative(e.cwd, dir) === ""), `an app-server ran in the paper's directory: ${JSON.stringify(servers)}`);
      const turn = heard().find((e) => e.in?.method === "turn/start").in.params;
      const prompt = turn.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(prompt.includes(`"thread_id": "${passage.id}"`), "the prompt names the thread");
      assert(prompt.includes("sections/method.tex"), `the passage's source line, computed on the computer: ${prompt.slice(0, 800)}`);
    });

    let caption = null;
    await step("remote-sends: in alice's reply on the NAS the @ menu lists Codex on the computer, and the reply's Send runs there the same way", async () => {
      caption = await commentOn(alice, PLACES.v1.figure.rect, "the caption says 2025");
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, caption.id);
        await replyField(thread);
        await page.keyboard.type("@cod");
        const menu = page.locator(".mentionMenu");
        const row = menu.locator(".refPopupItem.selected");
        await until(async () => (await row.locator(".refPopupText").innerText().catch(() => "")).startsWith("Codex"),
          { what: "the @ menu offers the folder's agent" });
        assertEq((await row.locator(".mentionAside").innerText()).trim(), `on ${name}`, "where it runs");
        await page.keyboard.press("Enter");
        await menu.waitFor({ state: "detached" });
        await thread.locator(".cmMentionChip", { hasText: "@codex" }).waitFor();
        await page.keyboard.type("fix this only");
        assertEq((await thread.locator("[data-agent-hint]").innerText()).trim(), `Sends this thread to Codex on ${name}.`,
          "the field says what sending does");
        await page.keyboard.press("Shift+Enter");
        const reply = await until(async () => (await threadsOf(pdf)).find((th) => th.id === caption.id)
          ?.comments.find((c) => c.body === "@codex fix this only"), { what: "the reply is stored" });
        const request = await doneRequest(reply.id);
        assertEq(JSON.stringify([request.note, request.threads.map((t) => t.id), request.state_by]),
          JSON.stringify(["fix this only", [caption.id], "Codex"]), "a Send of the thread, the comment's other words its instruction");
        // the reply's own @codex is the agent's chip, titled with the computer
        const chip = thread.locator(".commentItem.reply .mentionChip.mention-agent", { hasText: "@codex" }).first();
        await chip.waitFor();
        assertEq(await chip.getAttribute("title"), `Agent: Codex on ${name}`, "the chip names the computer");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      await answeredThere(caption.id);
    });

    let units = null;
    await step("remote-sends: bob sees no agent and his @codex is plain text that sends nothing; a Send made with a token is refused", async () => {
      assertEq(JSON.stringify(await computersOf(bob)), "[]", "the page's computers for another member");
      assertEq((await bob.api(`/api/feedback?scope=${folderId}`)).agent ?? null, null, "the feedback document names him no agent");
      units = await commentOn(alice, PLACES.v1.equation.rect, "the units are missing");
      const before = (await requestsOf(alice)).length;
      const ctx = await bob.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const thread = await openThread(page, units.id);
        await replyField(thread);
        await page.keyboard.type("@cod");
        await sleep(600);
        assertEq(await page.locator(".mentionMenu .refPopupItem", { hasText: "Codex" }).count(), 0, "the @ menu offers him no agent");
        await page.keyboard.type("ex please look");
        assertEq(await thread.locator("[data-agent-hint]").count(), 0, "his field says nothing of a Send");
        await page.keyboard.press("Shift+Enter");
        const reply = await until(async () => (await threadsOf(pdf)).find((th) => th.id === units.id)
          ?.comments.find((c) => c.body === "@codex please look"), { what: "bob's reply is stored" });
        const item = thread.locator(".commentItem.reply", { hasText: "please look" }).first();
        await item.waitFor();
        assertEq(await item.locator(".mentionChip").count(), 0, "his @codex is plain text");
        await sleep(1000);
        assertEq((await requestsOf(alice)).length, before, "no Send made");
        assert(!(await requestsOf(alice)).some((r) => r.id === reply.id), "none of his comment");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assert(!(await threadsOf(pdf)).find((th) => th.id === units.id).sent, "the thread carries no Sent chip");
      // a token's Send to the folder: the computer takes Sends made in Gamma alone
      const token = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "a script", scope: "write", expires_in_days: 1 } });
      const refused = await fetch(`${nas.base}/api/feedback/requests`, { method: "POST",
        headers: { Authorization: `Bearer ${token.token}`, "X-Gamma-Workspace": wsId, "Content-Type": "application/json" },
        body: JSON.stringify({ scope: pdf, threads: [units.id] }) });
      const why = await refused.text();
      assertEq(refused.status, 403, `a token's Send is refused: ${why}`);
      assert(why.includes(`Codex on ${name} takes Sends made in Gamma, not with a token.`), `with why: ${why}`);
      await alice.api(`/api/integrations/tokens/${token.id}`, { method: "DELETE" });
      assertEq((await requestsOf(alice)).length, before, "still no Send made");
    });

    await step("remote-sends: the computer stopped, alice's Send waits for it (Waiting for the computer), and runs once the computer is back", async () => {
      await computer.halt();
      // its held poll ends (at most the 25 s hold), then the grace
      await until(async () => (await computersOf(alice))[0]?.online === false,
        { timeout: 25000 + GRACE_S * 1000 + 15000, every: 500, what: "the NAS reads the computer offline" });
      const away = `${name} is offline: this runs when it connects.`;
      let request = null;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const d = await openFeedback(page);
        const send = d.getByRole("button", { name: "Send 1 comment to Codex", exact: true });
        await send.waitFor({ timeout: 10000 });
        await until(async () => (await notes(d)).includes(away), { what: `the note says the Send waits: ${JSON.stringify(await notes(d))}` });
        await send.click();
        const row = d.locator(".feedbackRequest").first();
        await row.waitFor({ timeout: 10000 });
        await until(async () => (await row.locator(".feedbackRequestState").innerText()).trim() === `Waiting for ${name}`,
          { what: `the request reads Waiting for ${name}: ${await row.locator(".feedbackRequestState").innerText().catch(() => "")}` });
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        const thread = await openThread(page, units.id);
        const chip = thread.locator(".uiTag[data-sent]").first();
        await until(async () => (await chip.innerText().catch(() => "")).trim() === `Waiting for ${name}`,
          { what: `the thread's chip reads Waiting for ${name}: ${await chip.innerText().catch(() => "")}` });
        assertEq(await chip.getAttribute("title"), away, "its title says it runs when the computer connects");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      request = (await requestsOf(alice)).find((r) => r.threads.some((t) => t.id === units.id));
      assertEq(request?.state, "sent", "the request waits, sent");
      await sleep(1000);
      assertEq((await requestsOf(alice)).find((r) => r.id === request.id).state, "sent", "and keeps waiting while the computer is away");

      // the computer back, on the same data: its channel announces, asks for the Sends that wait and runs this one
      await computer.start();
      const done = await doneRequest(request.id, 90000);
      assertEq(done.state_by, "Codex", "done by Codex");
      await answeredThere(units.id);
      assertEq((await computersOf(alice))[0]?.online, true, "the computer reads online again");
    });
  } finally {
    await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
